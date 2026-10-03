import adsk.core
import adsk.fusion
import traceback
import json
import math
import os
import tempfile
from datetime import datetime, timezone

# VEX Perfect Mirror 1.0.4
# Independent sketch snapshots around Fusion's native component mirror.
# All distances are in Fusion's internal cm units.
VERSION = '1.0.4'
CMD_ID = 'VEXPerfectMirror_Command'
REPORT_ID = 'VEXPerfectMirror_Report'
HELP_ID = 'VEXPerfectMirror_Help'
TAB_ID = 'VEXPerfectMirror_Tab'
PANEL_ID = 'VEXPerfectMirror_Panel'
WORKSPACE_ID = 'FusionSolidEnvironment'
CMD_NAME = 'VEX Perfect Mirror'
CMD_DESCRIPTION = 'Mirror components across a plane, preserving VEX cut states and fixed sketch dots.'
POINT_TOLERANCE_CM = 1e-5
CUT_MARGIN_CM = 0.35
RESOURCE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'resources', 'mirror')
_handlers = []
_toolbar_hooks = []
_last_report = None
_running = False


def _log(msg):
    try:
        adsk.log('[VEX Perfect Mirror] ' + str(msg))
    except:
        pass


def _iter_collection(collection):
    if collection is None:
        return []
    if isinstance(collection, (list, tuple)):
        return list(collection)
    return [collection.item(i) for i in range(collection.count)]


def _occ_transform(occ):
    """Returns component-local -> root/assembly transform."""
    try:
        return occ.transform2.copy()
    except:
        return occ.transform.copy()


class Reflection:
    """Reflection about dot(p - origin, unit_normal) == 0 in root space."""
    def __init__(self, origin=(0., 0., 0.), normal=(1., 0., 0.)):
        if not all(math.isfinite(v) for v in (*origin, *normal)):
            raise ValueError('The mirror plane has invalid coordinates.')
        length = math.sqrt(sum(v*v for v in normal))
        if length < 1e-12:
            raise ValueError('The mirror plane has an invalid normal.')
        self.origin = tuple(origin)
        self.normal = tuple(v/length for v in normal)

    def point(self, point):
        xyz = (point.x, point.y, point.z)
        distance = sum((v-o)*n for v, o, n in zip(xyz, self.origin, self.normal))
        return adsk.core.Point3D.create(*(v-2*distance*n for v, n in zip(xyz, self.normal)))

    def vector(self, vector):
        xyz = (vector.x, vector.y, vector.z)
        distance = sum(v*n for v, n in zip(xyz, self.normal))
        return adsk.core.Vector3D.create(*(v-2*distance*n for v, n in zip(xyz, self.normal)))

    def as_dict(self):
        return {'origin_cm': self.origin, 'unit_normal': self.normal}


def _mirror_point_yz(point):
    # Compatibility helper; production operations pass their selected reflection.
    return Reflection().point(point)


def _mirror_vector_yz(vector):
    return Reflection().vector(vector)


def _reflection_for_plane(entity, root):
    native = _native(entity)
    geometry = adsk.core.Plane.cast(native.geometry)
    if not geometry:
        raise ValueError('Choose a construction plane or a planar face.')
    origin, normal = geometry.origin.copy(), geometry.normal.copy()
    context = getattr(entity, 'assemblyContext', None)
    if context:
        transform = _occ_transform(context)
        origin.transformBy(transform)
        normal.transformBy(transform)
    else:
        owner = getattr(native, 'parentComponent', None)
        if owner is None and hasattr(native, 'body'):
            owner = native.body.parentComponent
        if owner is not None and owner != root:
            raise ValueError('Select the plane through its occurrence in the root assembly.')
    return Reflection((origin.x, origin.y, origin.z), (normal.x, normal.y, normal.z))


def _bbox_center(box):
    if not box:
        return None
    return adsk.core.Point3D.create(
        (box.minPoint.x + box.maxPoint.x) * 0.5,
        (box.minPoint.y + box.maxPoint.y) * 0.5,
        (box.minPoint.z + box.maxPoint.z) * 0.5,
    )


def _point_distance_sq(a, b):
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return dx * dx + dy * dy + dz * dz


def _occ_center(occ):
    try:
        return occ.physicalProperties.centerOfMass
    except Exception:
        pass
    try:
        return _bbox_center(occ.preciseBoundingBox)
    except:
        try:
            return _bbox_center(occ.boundingBox)
        except:
            return None


def _body_center(body):
    try:
        return body.physicalProperties.centerOfMass
    except Exception:
        pass
    try:
        return _bbox_center(body.preciseBoundingBox)
    except:
        try:
            return _bbox_center(body.boundingBox)
        except:
            return None


def _entity_token(entity):
    try:
        return entity.entityToken
    except:
        # Fallback is only for the lifetime of this command.
        return str(id(entity))


def _native(entity):
    try:
        native = entity.nativeObject
        return native if native else entity
    except:
        return entity


def _set_occurrence_lightbulb(occ, state):
    try:
        occ.isLightBulbOn = state
        return True
    except:
        return False


def _get_occurrence_lightbulb(occ):
    try:
        return occ.isLightBulbOn
    except:
        # isVisible includes parent visibility, so only use as a fallback.
        try:
            return occ.isVisible
        except:
            return True


def _get_body_lightbulb(body):
    try:
        return _native(body).isLightBulbOn
    except:
        try:
            return body.isLightBulbOn
        except:
            return True


def _set_body_lightbulb(body, state):
    try:
        _native(body).isLightBulbOn = state
        return True
    except:
        try:
            body.isLightBulbOn = state
            return True
        except:
            return False


def _geometry_signature(entity):
    """Orientation-independent checks; never use names as geometric evidence."""
    try:
        if hasattr(entity, 'bRepBodies'):
            return (entity.bRepBodies.count, entity.childOccurrences.count)
        return (entity.faces.count, entity.edges.count, bool(entity.isSolid))
    except Exception:
        return None


def _compatible_geometry(source, target):
    a, b = _geometry_signature(source), _geometry_signature(target)
    if a is not None and b is not None and a != b:
        return False
    try:
        a, b = source.physicalProperties.volume, target.physicalProperties.volume
        return abs(a-b) <= max(1e-6, max(abs(a), abs(b))*1e-4)
    except Exception:
        return True


def _pair_by_reflected_centers(source_items, target_items, center_func,
                               reflection=None, strict=False):
    reflection = reflection or Reflection()
    remaining = list(target_items)
    if strict and len(source_items) != len(remaining):
        raise RuntimeError('Source and mirror have different body/child counts.')
    pairs = []
    for source in source_items:
        wanted = center_func(source)
        if wanted is None:
            raise RuntimeError('Could not measure geometry for safe component/body matching.')
        wanted = reflection.point(wanted)
        candidates = []
        for i, target in enumerate(remaining):
            center = center_func(target)
            if center is not None and (not strict or _compatible_geometry(source, target)):
                candidates.append((_point_distance_sq(wanted, center), i))
        candidates.sort()
        if not candidates:
            raise RuntimeError('No compatible mirrored geometry was found.')
        score, index = candidates[0]
        if strict:
            # 0.01 mm centroid agreement. Refuse to guess for ambiguous geometry.
            if score > 1e-6:
                raise RuntimeError('Mirrored geometry did not pass its position check.')
            if len(candidates) > 1 and abs(candidates[1][0] - score) <= 1e-12:
                raise RuntimeError('Overlapping mirrored objects are ambiguous; mirror them separately.')
        pairs.append((source, remaining.pop(index)))
    return pairs


def _mirrored_body_pairs(source_occ, target_occ, reflection):
    """Verify visible stock; native mirrors may omit hidden offcut bodies."""
    sources = _iter_collection(source_occ.bRepBodies)
    remaining = _iter_collection(target_occ.bRepBodies)
    pairs = []
    # Required visible stock gets priority over optional hidden duplicates.
    sources.sort(key=lambda body: not _get_body_lightbulb(body))
    for source in sources:
        center = _body_center(source)
        wanted = reflection.point(center) if center is not None else None
        matches = []
        for index, target in enumerate(remaining):
            actual = _body_center(target)
            if wanted is not None and actual is not None and _compatible_geometry(source, target):
                distance = _point_distance_sq(wanted, actual)
                if distance <= 1e-6:
                    matches.append((distance, index))
        matches.sort()
        if not matches:
            if _get_body_lightbulb(source):
                raise RuntimeError('Visible body missing or different in mirror: {} '
                                   '(source bodies {}, output bodies {}).'.format(
                                       source_occ.name, source_occ.bRepBodies.count,
                                       target_occ.bRepBodies.count))
            continue
        if len(matches) > 1 and abs(matches[1][0] - matches[0][0]) <= 1e-12:
            raise RuntimeError('Overlapping mirrored bodies are ambiguous: ' + source_occ.name)
        pairs.append((source, remaining.pop(matches[0][1])))
    if remaining:
        raise RuntimeError('Unmatched output bodies in mirror: {} ({} extra).'.format(
            source_occ.name, len(remaining)))
    return pairs


def _restore_body_visibility(source_occ, target_occ, reflection=None, strict=False):
    reflection = reflection or Reflection()
    pairs = (_mirrored_body_pairs(source_occ, target_occ, reflection) if strict else
             _pair_by_reflected_centers(
                 _iter_collection(source_occ.bRepBodies), _iter_collection(target_occ.bRepBodies),
                 _body_center, reflection, False))
    for source, target in pairs:
        state = _get_body_lightbulb(source)
        if not _set_body_lightbulb(target, state) or _get_body_lightbulb(target) != state:
            raise RuntimeError('Could not restore a cut/hidden body state.')
    return len(pairs)


def _make_mirrored_sketch_transform(source_sketch, source_occ, target_occ, reflection=None):
    """Build a target sketch frame from an assembly-context source sketch.

    v0.1.3 deliberately uses a Sketch proxy in the source occurrence instead of
    reconstructing assembly context by hand. Fusion documents Sketch.origin,
    xDirection and yDirection as model-space values and Sketch proxies carry the
    occurrence context, so these values are already in ROOT model space.
    """
    source_proxy = source_sketch.createForAssemblyContext(source_occ)
    if not source_proxy:
        raise RuntimeError('Could not create source sketch assembly proxy.')

    # Source frame in root/model space.
    p0_world = source_proxy.origin.copy()
    x_world = source_proxy.xDirection.copy()
    y_world = source_proxy.yDirection.copy()

    reflection = reflection or Reflection()
    p0_world = reflection.point(p0_world)
    x_world = reflection.vector(x_world)
    y_world = reflection.vector(y_world)

    # Convert reflected frame from root/model space into target-component space.
    world_to_target = _occ_transform(target_occ)
    if not world_to_target.invert():
        raise RuntimeError('Could not invert target occurrence transform.')

    p0_local = p0_world.copy()
    p0_local.transformBy(world_to_target)

    x_local = x_world.copy()
    y_local = y_world.copy()
    x_local.transformBy(world_to_target)
    y_local.transformBy(world_to_target)

    if not x_local.normalize() or not y_local.normalize():
        raise RuntimeError('Invalid mirrored sketch axes.')

    # A reflection reverses handedness. Fusion sketch coordinates must remain a
    # right-handed coordinate system, so reconstruct Z from reflected X and Y.
    z_local = x_local.crossProduct(y_local)
    if not z_local or not z_local.normalize():
        raise RuntimeError('Could not construct mirrored sketch normal.')

    # Re-orthogonalize Y to remove floating-point drift.
    y_local = z_local.crossProduct(x_local)
    if not y_local or not y_local.normalize():
        raise RuntimeError('Could not orthogonalize mirrored sketch frame.')

    # setWithCoordinateSystem creates SKETCH -> TARGET COMPONENT. Sketch.transform
    # is the inverse mapping TARGET COMPONENT -> SKETCH, so invert before setting.
    sketch_to_target = adsk.core.Matrix3D.create()
    if not sketch_to_target.setWithCoordinateSystem(p0_local, x_local, y_local, z_local):
        raise RuntimeError('Could not construct mirrored sketch coordinate system.')

    target_component_to_sketch = sketch_to_target.copy()
    if not target_component_to_sketch.invert():
        raise RuntimeError('Could not invert mirrored sketch coordinate system.')

    return target_component_to_sketch


def _point_inside_box(point, box, margin=0.35):
    """Return True when a ROOT-space point lies in/near a ROOT-space bbox.

    Fusion internal distance units are cm. 0.35 cm = 3.5 mm; this intentionally
    gives hole-centre points some tolerance while still removing point rows that
    belong to VEX stock sections hidden beyond a cut end.
    """
    return (
        box.minPoint.x - margin <= point.x <= box.maxPoint.x + margin and
        box.minPoint.y - margin <= point.y <= box.maxPoint.y + margin and
        box.minPoint.z - margin <= point.z <= box.maxPoint.z + margin
    )


def _point_near_visible_source_geometry(source_occ, world_point, margin=CUT_MARGIN_CM):
    """Use component-aligned bounds so a rotated bar does not enlarge the filter.

    Only trim when this occurrence actually has hidden stock. Keep uncut models'
    construction dots and assemblies with no directly owned visible bodies.
    """
    try:
        bodies = _iter_collection(source_occ.bRepBodies)
        if not any(not _get_body_lightbulb(body) for body in bodies):
            return True
        visible = [body for body in bodies if _get_body_lightbulb(body)]
        if not visible:
            return True
        transform = _occ_transform(source_occ)
        if not transform.invert():
            return True
        local = world_point.copy()
        local.transformBy(transform)
        boxes = []
        for body in visible:
            native = _native(body)
            try:
                boxes.append(native.preciseBoundingBox)
            except Exception:
                boxes.append(native.boundingBox)
        return any(_point_inside_box(local, box, margin) for box in boxes if box)
    except Exception:
        # Inability to evaluate bounds must never silently delete useful points.
        return True


def _flag(entity, name):
    try:
        return bool(getattr(entity, name))
    except Exception:
        return False


def _is_reference(point):
    return _flag(point, 'isReference') or _flag(point, 'isLinked')


def _lock_point(point):
    # Reference geometry is driven by its fixed helper. Do not break its link.
    if _is_reference(point) or _flag(point, 'isFullyConstrained'):
        return True
    point.isFixed = True
    return _flag(point, 'isFixed') or _flag(point, 'isFullyConstrained')


def _matching_point(sketch, position):
    # Never reuse the invisible built-in origin: it would hide the new dot.
    matches = []
    for point in _iter_collection(sketch.sketchPoints):
        if point == sketch.originPoint:
            continue
        if _point_distance_sq(point.geometry, position) <= POINT_TOLERANCE_CM ** 2:
            matches.append(point)
    return next((p for p in matches if _is_reference(p)), matches[0] if matches else None)


class _ProjectionCleanupError(RuntimeError):
    pass


def _project_fixed_anchor(target_sketch, anchor_sketch, position):
    """A real local projection, not an invented isReference/color assignment.

    The anchor is a fixed snapshot in an earlier, hidden sketch with the same
    frame. No dependency is created back to the original mechanism.
    """
    if anchor_sketch is None or abs(position.z) > POINT_TOLERANCE_CM:
        raise RuntimeError('No planar projection anchor available; use a fixed snapshot.')
    anchor = anchor_sketch.sketchPoints.add(position)
    if not _lock_point(anchor):
        raise RuntimeError('Could not fix the reference anchor.')
    before = list(_iter_collection(target_sketch.sketchPoints))
    try:
        if hasattr(target_sketch, 'project2'):
            entities = target_sketch.project2([anchor], True)
        else:
            entities = _iter_collection(target_sketch.project(anchor))
        for entity in entities or []:
            point = adsk.fusion.SketchPoint.cast(entity)
            if (point and point != target_sketch.originPoint and _is_reference(point) and
                    _point_distance_sq(point.geometry, position) <= POINT_TOLERANCE_CM ** 2):
                return point
        raise RuntimeError('Fusion did not return a linked point at the required position.')
    except Exception:
        # A failed projection must not leave an extra or flattened dot behind.
        try:
            for point in reversed(_iter_collection(target_sketch.sketchPoints)):
                if not any(point == old for old in before):
                    if _is_reference(point):
                        point.isReference = False
                    if not point.deleteMe():
                        raise RuntimeError('Could not remove an unsuccessful projection.')
        except Exception as cleanup_error:
            raise _ProjectionCleanupError(str(cleanup_error)) from cleanup_error
        raise


def _copy_points_world_exact(source_sketch, target_sketch, anchor_sketch,
                             source_occ, target_occ, prune_cut_points, stats):
    count = 0
    target_proxy = target_sketch.createForAssemblyContext(target_occ)
    if not target_proxy:
        raise RuntimeError('Could not create target sketch assembly proxy.')

    for point_index, source in enumerate(_iter_collection(source_sketch.sketchPoints)):
        if point_index % 50 == 0:
            _check_cancel(stats)
        is_origin = source == source_sketch.originPoint
        if is_origin and not stats['options']['include_origins']:
            stats['origins_skipped'] += 1
            continue
        record = {'sketch': source_sketch.name, 'occurrence': source_occ.fullPathName,
                  'origin': is_origin, 'reference': _is_reference(source),
                  'source_fixed': _flag(source, 'isFixed'),
                  'source_fully_constrained': _flag(source, 'isFullyConstrained')}
        stats['points'].append(record)
        if record['reference']:
            stats['reference_points'] += 1
        if is_origin:
            stats['origin_points'] += 1
        try:
            # Fusion can return None rather than an empty list for VEX dots.
            connections = source.connectedEntities
            connected = connections is not None and connections.count > 0
            record['connected_to_curve'] = connected
            source_proxy = source.createForAssemblyContext(source_occ)
            if not source_proxy:
                raise RuntimeError('Could not create source point assembly proxy.')
            world = source_proxy.worldGeometry
            wanted = stats.get('reflection', Reflection()).point(world)
            record['source_world_cm'] = [world.x, world.y, world.z]
            record['wanted_world_cm'] = [wanted.x, wanted.y, wanted.z]
            # Curves are copied intact. Do not prune their structural points.
            if (prune_cut_points and not connected and
                    not _point_near_visible_source_geometry(
                        source_occ, world, stats['options']['prune_margin_cm'])):
                stats['pruned_points'] += 1
                record['status'] = 'pruned'
                continue
            position = target_proxy.modelToSketchSpace(wanted)
            target = _matching_point(target_sketch, position)
            if target:
                stats['reused_points'] += 1
                record['method'] = 'existing curve/point'
            else:
                if record['reference'] or is_origin:
                    try:
                        target = _project_fixed_anchor(target_sketch, anchor_sketch, position)
                        record['method'] = 'projection from fixed local anchor'
                    except _ProjectionCleanupError:
                        raise
                    except Exception as ex:
                        record['projection_note'] = str(ex)
                if not target:
                    target = target_sketch.sketchPoints.add(position)
                    record['method'] = 'fixed snapshot'
                count += 1
            if (record['reference'] or is_origin) and not _is_reference(target):
                stats['reference_fallbacks'] += 1
            elif _is_reference(target):
                stats['projected_points'] += 1
            if not _lock_point(target):
                raise RuntimeError('Fusion did not fix or constrain this point.')
            # Verify now and once more after base-feature edit/compute finishes.
            actual = target.createForAssemblyContext(target_occ).worldGeometry
            if _point_distance_sq(actual, wanted) > POINT_TOLERANCE_CM ** 2:
                raise RuntimeError('Mirrored point failed the root-space position check.')
            stats['verification'].append((target, target_occ, wanted, record))
            record['status'] = 'pending verification'
        except Exception as ex:
            stats['point_failures'] += 1
            record['status'] = 'failed'
            record['error'] = str(ex)
            _log('Point copy failed: {}'.format(ex))
    return count


def _verify_points(stats):
    for point, occurrence, wanted, record in stats['verification']:
        try:
            actual = point.createForAssemblyContext(occurrence).worldGeometry
            error_cm = _point_distance_sq(actual, wanted) ** 0.5
            record['error_cm'] = error_cm
            if error_cm > POINT_TOLERANCE_CM:
                raise RuntimeError('Point moved after compute: {} cm'.format(error_cm))
            if not _lock_point(point):
                raise RuntimeError('Point is still movable after compute.')
            record['status'] = 'verified'
            stats['verified_points'] += 1
        except Exception as ex:
            stats['point_failures'] += 1
            record['status'] = 'failed'
            record['error'] = str(ex)
    stats['verification'].clear()


def _save_report(stats):
    global _last_report
    # One unique report per run; no design file or original geometry is modified.
    try:
        report = {key: value for key, value in stats.items()
                  if key not in ('processed_component_pairs', 'verification', 'reflection', 'progress', 'occurrence_pairs')}
        report['version'] = VERSION
        report['created_utc'] = datetime.now(timezone.utc).isoformat()
        fd, path = tempfile.mkstemp(prefix='VEXPerfectMirror_' + VERSION + '_', suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
        stats['report_path'] = path
        _last_report = path
        _log('Diagnostic report: ' + path)
    except Exception as ex:
        stats['report_path'] = 'Report could not be saved: ' + str(ex)


def _collect_copyable_sketch_curves(sketch):
    """Collect sketch curves only. Standalone points are copied separately."""
    entities = adsk.core.ObjectCollection.create()
    curves = sketch.sketchCurves
    for i in range(curves.count):
        if not entities.add(curves.item(i)):
            raise RuntimeError('Could not collect a sketch curve for copying.')
    return entities


def _copy_sketch_display_state(source, target):
    for prop in (
        'isLightBulbOn',
        'arePointsShown',
        'areProfilesShown',
        'areConstraintsShown',
        'areDimensionsShown',
    ):
        try:
            setattr(target, prop, getattr(source, prop))
        except:
            pass


def _copy_sketches_for_component_pair(source_occ, target_occ, design, prune_cut_points, stats):
    source_comp = source_occ.component
    target_comp = target_occ.component
    if source_comp == target_comp:
        raise RuntimeError('Mirrored occurrence shares the source component; sketch repair stopped.')

    # Avoid recreating sketches multiple times if several occurrences reference
    # the same source/target component definitions.
    pair_key = (source_comp, target_comp)
    if pair_key in stats['processed_component_pairs']:
        return
    stats['processed_component_pairs'].append(pair_key)

    source_sketches = source_comp.sketches
    if source_sketches.count == 0:
        return

    is_parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    base_feature = None

    try:
        if is_parametric:
            base_feature = target_comp.features.baseFeatures.add()
            try:
                base_feature.name = 'VEX Perfect Mirror - Sketches'
            except:
                pass
            if not base_feature.startEdit():
                raise RuntimeError('Could not enter the sketch base feature.')

        for i in range(source_sketches.count):
            source_sketch = source_sketches.item(i)
            _check_cancel(stats)
            try:
                anchor_sketch = None
                needs_anchor = any(_is_reference(p) or
                                   (stats['options']['include_origins'] and p == source_sketch.originPoint)
                                   for p in _iter_collection(source_sketch.sketchPoints))
                if needs_anchor:
                    try:
                        if is_parametric:
                            anchor_sketch = target_comp.sketches.addToBaseOrFormFeature(
                                target_comp.xYConstructionPlane, base_feature, False)
                        else:
                            anchor_sketch = target_comp.sketches.add(target_comp.xYConstructionPlane)
                        anchor_sketch.name = source_sketch.name + ' [Mirror anchors - keep hidden]'
                        anchor_sketch.transform = _make_mirrored_sketch_transform(
                            source_sketch, source_occ, target_occ, stats['reflection'])
                        anchor_sketch.isLightBulbOn = False
                    except Exception as ex:
                        stats['warnings'].append('Anchor sketch unavailable: ' + str(ex))
                        anchor_sketch = None
                if is_parametric:
                    target_sketch = target_comp.sketches.addToBaseOrFormFeature(
                        target_comp.xYConstructionPlane,
                        base_feature,
                        False,
                    )
                else:
                    target_sketch = target_comp.sketches.add(target_comp.xYConstructionPlane)

                target_sketch.name = source_sketch.name + ' [Mirror]'
                target_sketch.transform = _make_mirrored_sketch_transform(
                    source_sketch,
                    source_occ,
                    target_occ,
                    stats['reflection'],
                )

                copied_count = 0

                # Copy curves via Fusion so transferable constraints/dimensions
                # come across when possible.
                curves = _collect_copyable_sketch_curves(source_sketch)
                if curves.count > 0:
                    identity = adsk.core.Matrix3D.create()
                    # The reflected sketch normal flips; invert local Z for 3D curves.
                    if any(not curve.is2D for curve in _iter_collection(curves)):
                        identity.setCell(2, 2, -1)
                    try:
                        result = source_sketch.copy(curves, identity, target_sketch)
                        if not result or result.count == 0:
                            raise RuntimeError('Fusion returned no copied curves.')
                        copied_count += result.count
                    except Exception as ex:
                        stats['curve_failures'] += 1
                        _log('Curve copy failed for "{}": {}'.format(source_sketch.name, ex))

                # Audit ALL points, including reference origins and curve points.
                copied_count += _copy_points_world_exact(
                    source_sketch,
                    target_sketch,
                    anchor_sketch,
                    source_occ,
                    target_occ,
                    prune_cut_points,
                    stats,
                )

                _copy_sketch_display_state(source_sketch, target_sketch)
                for collection_name in ('sketchTexts',):
                    collection = getattr(source_sketch, collection_name, None)
                    if collection is not None and collection.count:
                        stats['warnings'].append('Sketch text was not copied: ' + source_sketch.name)
                stats['sketches'] += 1
                stats['sketch_entities'] += copied_count

            except _Cancelled:
                raise
            except Exception as ex:
                stats['sketch_failures'] += 1
                _log('Sketch copy failed for "{}": {}'.format(source_sketch.name, ex))

    finally:
        if base_feature:
            try:
                if not base_feature.finishEdit():
                    stats['sketch_failures'] += 1
                    stats['warnings'].append('Fusion did not finish the sketch base feature.')
            except Exception as ex:
                stats['sketch_failures'] += 1
                stats['warnings'].append('Could not finish sketch base feature: ' + str(ex))


class _Cancelled(RuntimeError):
    pass


def _check_cancel(stats):
    progress = stats.get('progress')
    if progress:
        adsk.doEvents()
        if progress.wasCancelled:
            raise _Cancelled('Mirror cancelled. Fusion will discard this command’s changes.')


def _collect_occurrence_pairs(source, target, reflection):
    if source.component == target.component:
        raise RuntimeError('Fusion reused a source component definition; repair would change the original.')
    # Aggregate occurrence volume/body count includes hidden stock. Check actual
    # reflected bodies instead; omitted hidden stock is an acceptable native result.
    _mirrored_body_pairs(source, target, reflection)
    result = [(source, target)]
    children = _iter_collection(source.childOccurrences)
    remaining = _iter_collection(target.childOccurrences)
    if len(children) != len(remaining):
        raise RuntimeError('Child component counts differ for {}: source {}, output {}.'.format(
            source.name, len(children), len(remaining)))
    # Verify candidate subtrees by their reflected bodies, rather than aggregate
    # centroids that can shift when hidden offcuts are omitted.
    for child in children:
        matches, reasons = [], []
        for index, candidate in enumerate(remaining):
            try:
                matches.append((index, _collect_occurrence_pairs(child, candidate, reflection)))
            except RuntimeError as ex:
                reasons.append(str(ex))
        if len(matches) != 1:
            detail = reasons[0] if not matches and reasons else 'Multiple matching subassemblies.'
            raise RuntimeError('Cannot identify mirrored child {}: {}'.format(child.name, detail))
        index, verified = matches[0]
        remaining.pop(index)
        result.extend(verified)
    return result


def _pair_children(source_occ, target_occ, reflection=None):
    return _pair_by_reflected_centers(
        _iter_collection(source_occ.childOccurrences), _iter_collection(target_occ.childOccurrences),
        _occ_center, reflection, strict=True)


def _new_stats(reflection, progress):
    stats = {key: 0 for key in (
        'top_level', 'body_visibility', 'occurrence_visibility', 'sketches',
        'sketch_entities', 'sketch_failures', 'point_failures', 'pruned_points',
        'curve_failures', 'reference_points', 'origin_points', 'origins_skipped',
        'reference_fallbacks', 'projected_points', 'reused_points', 'verified_points',
        'rigid_joints', 'rigid_groups', 'grounded_occurrences')}
    stats.update({
        'processed_component_pairs': [], 'verification': [], 'points': [],
        'warnings': [], 'relationships_omitted': [], 'occurrence_pairs': [],
        'reflection': reflection, 'plane': reflection.as_dict(), 'progress': progress,
        'options': {'include_origins': True, 'prune_margin_cm': CUT_MARGIN_CM,
                    'copy_sketches': True, 'restore_visibility': True, 'prune_cut_points': True},
        'status': 'running',
    })
    return stats


def _normalize_selection(source_occs, root):
    if not source_occs:
        raise ValueError('Select at least one component.')
    unique = []
    candidates = _iter_collection(getattr(root, 'allOccurrences', root.occurrences))
    for entity in source_occs:
        if entity is None:
            raise ValueError('Fusion returned an empty component selection. '
                             'Cancel and select the top-level assembly in the Browser again.')
        kind = getattr(entity, 'objectType', type(entity).__name__)
        name = getattr(entity, 'name', '(unnamed)')
        if not getattr(entity, 'isValid', True):
            raise ValueError('The selection is stale: {} ({}). '
                             'Cancel and select it again in the Browser.'.format(name, kind))
        occurrence = adsk.fusion.Occurrence.cast(entity)
        if occurrence is None:
            component = adsk.fusion.Component.cast(entity)
            if component is None:
                raise ValueError('Expected a component instance, received {} ({}). '
                                 'Select a component instance in the Browser.'.format(name, kind))
            if component == root:
                raise ValueError('The document root cannot be mirrored. '
                                 'Select its top-level component rows, such as left:1.')
            matches = [o for o in candidates if o.component == component]
            if len(matches) != 1:
                raise ValueError('Component {} has {} assembly instances. '
                                 'Select the specific instance in the Browser.'.format(name, len(matches)))
            occurrence = matches[0]
        # Resolve equivalent wrappers in the root assembly context. Never promote
        # a nested part to its parent or choose among repeated instances by name.
        matches = [candidate for candidate in candidates
                   if candidate == occurrence or candidate.fullPathName == occurrence.fullPathName]
        if not matches:
            native = _native(occurrence)
            matches = [candidate for candidate in candidates if _native(candidate) == native]
        if len(matches) != 1:
            raise ValueError('Select a specific component instance in the Browser of this design.')
        occurrence = matches[0]
        if not any(occurrence.fullPathName == old.fullPathName for old in unique):
            unique.append(occurrence)
    # A selected parent's subtree already includes its selected descendants.
    return [occurrence for occurrence in unique if not any(
        occurrence.fullPathName.startswith(parent.fullPathName + '+')
        for parent in unique if parent != occurrence)]


def _preflight_subtree(occurrence):
    component = occurrence.component
    if _iter_collection(component.meshBodies):
        raise ValueError('Mesh components are not supported: ' + occurrence.name)
    # External links are never broken automatically. Native Fusion may reject them.
    for child in _iter_collection(occurrence.childOccurrences):
        _preflight_subtree(child)


def _mapped_occurrence(source, pairs):
    if source is None:
        return None
    for original, mirrored in pairs:
        if original == source:
            return mirrored
    # Fusion can provide equivalent occurrence wrappers; fullPathName identifies
    # one occurrence in this root context. Never match by component definition alone.
    path = source if isinstance(source, str) else source.fullPathName
    matches = [mirrored for original, mirrored in pairs if original.fullPathName == path]
    return matches[0] if len(matches) == 1 else None


def _omit_relationship(stats, name, reason):
    stats['relationships_omitted'].append({'name': name, 'reason': reason})


def _relationship_snapshot(entity, group=False):
    """Read relationship data before native feature creation can invalidate proxies."""
    if isinstance(entity, dict):
        return entity
    record = {'name': '(unreadable relationship)', 'suppressed': False}
    try:
        record['name'] = entity.name
        record['suppressed'] = entity.isSuppressed
        if group:
            record['members'] = [o.fullPathName for o in _iter_collection(entity.occurrences)]
        else:
            one, two = entity.occurrenceOne, entity.occurrenceTwo
            record['one'] = one.fullPathName if one is not None else None
            record['two'] = two.fullPathName if two is not None else None
            motion = entity.jointMotion
            record['type'] = motion.jointType if motion is not None else None
            record['visible'] = getattr(entity, 'isLightBulbOn', None)
    except Exception as ex:
        record['error'] = 'Fusion could not read relationship data: ' + str(ex)
    return record


def _copy_rigid_relationships(root, source_joints, source_groups, stats):
    pairs = stats['occurrence_pairs']
    rigid_type = adsk.fusion.JointTypes.RigidJointType
    for original in source_joints:
        _check_cancel(stats)
        joint = _relationship_snapshot(original)
        if 'error' in joint:
            _omit_relationship(stats, joint['name'], joint['error'])
            continue
        one = _mapped_occurrence(joint['one'], pairs)
        two = _mapped_occurrence(joint['two'], pairs)
        if one is None and two is None:
            continue
        if one is None or two is None:
            _omit_relationship(stats, joint['name'], 'Connects to an unselected component or the root.')
            continue
        if joint['type'] != rigid_type:
            _omit_relationship(stats, joint['name'], 'Moving joints require motion-axis/limit reconstruction; not copied.')
            continue
        # Read current relationships defensively too: root collections can still
        # contain source proxies whose occurrence getters no longer work.
        equivalent = []
        for current in list(root.allJoints) + list(root.allAsBuiltJoints):
            candidate = _relationship_snapshot(current)
            if 'error' in candidate:
                _log(candidate['error'])
                continue
            if {candidate['one'], candidate['two']} == {one.fullPathName, two.fullPathName}:
                equivalent.append(candidate)
        if equivalent:
            if not all(j['type'] == rigid_type and j['suppressed'] == joint['suppressed'] for j in equivalent):
                raise RuntimeError('Fusion generated an incompatible relationship: ' + joint['name'])
            stats['rigid_joints'] += 1
            continue
        data = root.asBuiltJoints.createInput(one, two, None)
        if not data or not data.setAsRigidJointMotion():
            raise RuntimeError('Could not define the mirrored rigid joint: ' + joint['name'])
        created = root.asBuiltJoints.add(data)
        if not created:
            raise RuntimeError('Could not create the mirrored rigid joint: ' + joint['name'])
        created.name = joint['name'] + ' [Mirror]'
        if joint['suppressed']:
            created.isSuppressed = True
        if joint['visible'] is not None:
            created.isLightBulbOn = joint['visible']
        stats['rigid_joints'] += 1
    for original in source_groups:
        _check_cancel(stats)
        group = _relationship_snapshot(original, group=True)
        if 'error' in group:
            _omit_relationship(stats, group['name'], group['error'])
            continue
        members = [_mapped_occurrence(path, pairs) for path in group['members']]
        if not any(member is not None for member in members):
            continue
        if any(member is None for member in members):
            _omit_relationship(stats, group['name'], 'Rigid group includes an unselected occurrence.')
            continue
        expected = sorted(m.fullPathName for m in members)
        existing = []
        for current in root.allRigidGroups:
            candidate = _relationship_snapshot(current, group=True)
            if 'error' not in candidate and sorted(candidate['members']) == expected:
                existing.append(candidate)
        if existing:
            if any(g['suppressed'] != group['suppressed'] for g in existing):
                raise RuntimeError('Fusion generated a rigid group with different suppression state.')
            stats['rigid_groups'] += 1
            continue
        collection = adsk.core.ObjectCollection.create()
        for member in members:
            collection.add(member)
        created = root.rigidGroups.add(collection, False)
        if not created:
            raise RuntimeError('Could not recreate rigid group: ' + group['name'])
        created.name = group['name'] + ' [Mirror]'
        if group['suppressed']:
            created.isSuppressed = True
        stats['rigid_groups'] += 1


def _restore_component_details(source, target, stats):
    source_comp, target_comp = source.component, target.component
    for prop in ('description', 'partNumber'):
        try:
            setattr(target_comp, prop, getattr(source_comp, prop))
        except Exception as ex:
            stats['warnings'].append('Could not copy {} for {}: {}'.format(prop, source.name, ex))
    for prop in ('isBodiesFolderLightBulbOn', 'isSketchFolderLightBulbOn'):
        if hasattr(source_comp, prop):
            try:
                setattr(target_comp, prop, getattr(source_comp, prop))
            except Exception as ex:
                stats['warnings'].append('Folder visibility: ' + str(ex))
    if not _set_occurrence_lightbulb(target, _get_occurrence_lightbulb(source)):
        raise RuntimeError('Could not restore component visibility: ' + source.name)
    stats['occurrence_visibility'] += 1
    target.isGrounded = source.isGrounded
    if target.isGrounded != source.isGrounded:
        raise RuntimeError('Could not restore grounding: ' + source.name)
    stats['grounded_occurrences'] += int(source.isGrounded)


def _mirror_selected_occurrences(source_occs, plane_entity, progress=None):
    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    if not design:
        raise ValueError('Open a Fusion Design first.')
    edit_object = adsk.core.Application.get().activeEditObject
    if edit_object is not None and not adsk.fusion.Component.cast(edit_object):
        raise ValueError('Finish editing the current sketch or feature before mirroring.')
    root = design.rootComponent
    source_occs = _normalize_selection(source_occs, root)
    reflection = _reflection_for_plane(plane_entity, root)
    for source in source_occs:
        _preflight_subtree(source)
    stats = _new_stats(reflection, progress)
    original_definitions = [o.component for o in _iter_collection(root.allOccurrences)]
    source_joints = [_relationship_snapshot(j) for j in list(root.allJoints) + list(root.allAsBuiltJoints)]
    source_groups = [_relationship_snapshot(g, group=True) for g in root.allRigidGroups]
    old_active = design.activeOccurrence
    try:
        if not design.activateRootComponent():
            raise RuntimeError('Could not activate the root assembly.')
        # Create one native mirror per selection. Each output is unambiguously
        # associated with its source, even with repeated component instances.
        for index, source in enumerate(source_occs):
            if progress:
                progress.message = 'Mirroring component {} of {}'.format(index+1, len(source_occs))
                progress.progressValue = int(50*index/len(source_occs))
            _check_cancel(stats)
            before = _iter_collection(root.occurrences)
            collection = adsk.core.ObjectCollection.create()
            collection.add(source)
            features = root.features.mirrorFeatures
            data = features.createInput(collection, plane_entity)
            feature = features.add(data)
            if not feature:
                raise RuntimeError('Fusion could not mirror: ' + source.name)
            feature.name = 'VEX Mirror — ' + source.name
            if not design.computeAll():
                raise RuntimeError('Fusion could not compute the mirrored component.')
            created = [o for o in _iter_collection(root.occurrences)
                       if not any(o == previous for previous in before)]
            if len(created) != 1:
                raise RuntimeError('Could not identify exactly one output for: ' + source.name)
            target = created[0]
            if any(target.component == o.component for o in before):
                raise RuntimeError('Fusion reused an existing component; repair stopped to protect it.')
            target.component.name = source.component.name + ' [Mirror]'
            pairs = _collect_occurrence_pairs(source, target, reflection)
            stats['occurrence_pairs'].extend(pairs)
            stats['top_level'] += 1
        # Validate ALL definitions before modifying any child sketches/metadata.
        for source, target in stats['occurrence_pairs']:
            if any(target.component == component for component in original_definitions):
                raise RuntimeError('A mirrored child still uses an original component definition.')
        total = len(stats['occurrence_pairs'])
        for index, (source, target) in enumerate(stats['occurrence_pairs']):
            _check_cancel(stats)
            if progress:
                progress.message = 'Restoring sketches and cut states: ' + source.name
                progress.progressValue = 50 + int(35*index/max(1, total))
            stats['body_visibility'] += _restore_body_visibility(source, target, reflection, strict=True)
            omitted = source.bRepBodies.count - target.bRepBodies.count
            if omitted > 0:
                stats['warnings'].append('{}: Fusion omitted {} hidden bodies; visible stock verified.'.format(
                    source.name, omitted))
            _copy_sketches_for_component_pair(source, target, design, True, stats)
            _restore_component_details(source, target, stats)
        if progress:
            progress.message = 'Restoring rigid relationships and verifying result'
            progress.progressValue = 90
        _copy_rigid_relationships(root, source_joints, source_groups, stats)
        if not design.computeAll():
            raise RuntimeError('Fusion reported a final compute failure.')
        _verify_points(stats)
        # Recheck bodies/visibility after relationship solving, which can move parts.
        for source, target in stats['occurrence_pairs']:
            _restore_body_visibility(source, target, reflection, strict=True)
        failures = stats['point_failures'] + stats['sketch_failures'] + stats['curve_failures']
        if failures:
            raise RuntimeError('{} sketch/point/curve errors; the mirror was not accepted.'.format(failures))
        stats['status'] = 'completed'
        if progress:
            progress.progressValue = 100
        return stats
    except Exception as ex:
        stats['status'] = 'cancelled' if isinstance(ex, _Cancelled) else 'failed'
        stats['error'] = str(ex)
        stats['traceback'] = traceback.format_exc()
        raise
    finally:
        _save_report(stats)
        if old_active:
            try:
                old_active.activate()
            except Exception:
                pass


def _result_message(stats):
    omitted = len(stats['relationships_omitted'])
    return 'Copy successful.\n{} relationships could not be copied over.'.format(omitted)


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        global _running, _last_report
        if _running:
            args.executeFailed = True
            args.executeFailedMessage = 'A mirror is already running.'
            return
        app = adsk.core.Application.get()
        ui = app.userInterface
        progress = None
        _running = True
        _last_report = None
        try:
            inputs = args.firingEvent.sender.commandInputs
            selection = inputs.itemById('occurrences')
            plane = inputs.itemById('plane')
            if plane.selectionCount != 1:
                raise ValueError('Select one mirror plane.')
            # Preserve entity type: an early failed cast used to become None and
            # was misleadingly reported as a deleted component. Resolve before
            # opening the progress dialog, while the selection is still current.
            selected = [selection.selection(i).entity for i in range(selection.selectionCount)]
            design = adsk.fusion.Design.cast(app.activeProduct)
            if design is None:
                raise ValueError('Open a Fusion Design first.')
            occurrences = _normalize_selection(selected, design.rootComponent)
            progress = ui.createProgressDialog()
            progress.isCancelButtonShown = True
            progress.cancelButtonText = 'Cancel mirror'
            progress.show(CMD_NAME, 'Checking selection', 0, 100, 0)
            stats = _mirror_selected_occurrences(occurrences, plane.selection(0).entity, progress)
            progress.hide()
            ui.messageBox(_result_message(stats), CMD_NAME)
        except Exception as ex:
            # Fusion's command transaction aborts on executeFailed, including
            # native mirrors, base features and relationship creation.
            args.executeFailed = True
            args.executeFailedMessage = str(ex) + '\nNo partial result will be kept.'
            if _last_report:
                args.executeFailedMessage += '\nDiagnostic report: ' + _last_report
            _log(traceback.format_exc())
        finally:
            if progress:
                progress.hide()
            _running = False


class _ValidateHandler(adsk.core.ValidateInputsEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        inputs = args.inputs
        args.areInputsValid = (inputs.itemById('occurrences').selectionCount > 0 and
                               inputs.itemById('plane').selectionCount == 1)


class _DestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self, handlers):
        super().__init__()
        self.handlers = handlers

    def notify(self, args):
        for handler in self.handlers:
            if handler in _handlers:
                _handlers.remove(handler)
        self.handlers.clear()


class _ActivateHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
        self.initialized = False

    def notify(self, args):
        if self.initialized:
            return
        self.initialized = True
        command = args.firingEvent.sender
        plane = command.commandInputs.itemById('plane')
        # Clear Fusion's inherited preselection as well as the old plane default.
        # First activation only; later activations preserve the user's choices.
        plane.clearSelection()
        command.commandInputs.itemById('occurrences').clearSelection()
        command.commandInputs.itemById('occurrences').hasFocus = True


class _CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()

    def notify(self, args):
        ui = adsk.core.Application.get().userInterface
        try:
            command = args.command
            command.isRepeatable = False
            command.isExecutedWhenPreEmpted = False
            owned = []
            for event, handler in ((command.execute, _ExecuteHandler()),
                                   (command.validateInputs, _ValidateHandler()),
                                   (command.activate, _ActivateHandler())):
                event.add(handler)
                owned.append(handler)
            destroy = _DestroyHandler(owned)
            command.destroy.add(destroy)
            owned.append(destroy)
            _handlers.extend(owned)
            inputs = command.commandInputs
            selection = inputs.addSelectionInput('occurrences', 'Components',
                                                 'Select one or more components or subassemblies')
            selection.addSelectionFilter('Occurrences')
            selection.setSelectionLimits(1, 0)
            plane = inputs.addSelectionInput('plane', 'Mirror plane',
                                             'Choose a construction plane or a planar face')
            plane.addSelectionFilter('ConstructionPlanes')
            plane.addSelectionFilter('PlanarFaces')
            plane.setSelectionLimits(1, 1)
            selection.hasFocus = True
        except Exception:
            ui.messageBox('Could not open Mirror:\n' + traceback.format_exc(), CMD_NAME)


class _UtilityExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, report):
        super().__init__()
        self.report = report

    def notify(self, args):
        ui = adsk.core.Application.get().userInterface
        if not self.report:
            ui.messageBox(
                'VEX Perfect Mirror {}\n\n'
                'Select components, choose a plane, and click OK.\n'
                'A selected subassembly includes its children. Both selections start empty.\n\n'
                'Sketch recovery, fixed dots and hidden cut-body restoration are automatic. '
                'Rigid joints/groups are recreated where both sides are selected. '
                'Moving joints and connections outside the selection are reported as omitted.\n\n'
                'Sketch points are snapshots: recreate the mirror after source edits.\n'
                'Use Undo to remove a successful operation.\n\n'
                'Startup is enabled in the manifest. Existing installations may require '
                'Run on Startup to be enabled once in Scripts and Add-Ins.'
                .format(VERSION), CMD_NAME)
            return
        if not _last_report or not os.path.isfile(_last_report):
            ui.messageBox('No report from this session is available yet.', CMD_NAME)
            return
        try:
            with open(_last_report, encoding='utf-8') as stream:
                report = json.load(stream)
            lines = ['Version {} — {}'.format(report['version'], report['status']),
                     'Report: ' + _last_report]
            if report.get('error'):
                lines.append(report['error'])
            lines.extend('{}: {}'.format(item['name'], item['reason'])
                         for item in report.get('relationships_omitted', []))
            lines.extend(report.get('warnings', []))
            if len(lines) == 2:
                lines.append('No errors or omitted relationships. Fixed fallbacks: {}'.format(
                    report.get('reference_fallbacks', 0)))
            ui.messageBox('\n\n'.join(lines[:22]) +
                          ('\n\nAdditional details are in the JSON report.' if len(lines) > 22 else ''),
                          CMD_NAME)
        except Exception as ex:
            ui.messageBox('Could not read the report: ' + str(ex), CMD_NAME)


class _UtilityCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, report):
        super().__init__()
        self.report = report

    def notify(self, args):
        handler = _UtilityExecuteHandler(self.report)
        owned = [handler]
        destroy = _DestroyHandler(owned)
        owned.append(destroy)
        args.command.execute.add(handler)
        args.command.destroy.add(destroy)
        _handlers.extend(owned)


def _remove_controls(ui, ids):
    for panel in _iter_collection(ui.allToolbarPanels):
        for command_id in ids:
            control = panel.controls.itemById(command_id)
            if control:
                control.deleteMe()


def _mount_toolbar(ui):
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if not workspace:
        return
    # Discover the other add-in's tab by display name, without assuming its ID.
    vex_tab = next((tab for tab in _iter_collection(workspace.toolbarTabs)
                    if ''.join(c for c in tab.name.casefold() if c.isalnum()) == 'vexcadlibrary'), None)
    if vex_tab:
        panel = vex_tab.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            panel = vex_tab.toolbarPanels.add(PANEL_ID, 'Mirror')
        for command_id in (CMD_ID, REPORT_ID, HELP_ID):
            if not panel.controls.itemById(command_id):
                control = panel.controls.addCommand(ui.commandDefinitions.itemById(command_id))
                if command_id == CMD_ID:
                    control.isPromotedByDefault = True
                    control.isPromoted = True
    # Keep a reliable fallback and a way to reach diagnostics without a new tab.
    panel = ui.allToolbarPanels.itemById('SolidCreatePanel')
    if panel:
        for command_id in (CMD_ID, REPORT_ID, HELP_ID):
            if not panel.controls.itemById(command_id):
                control = panel.controls.addCommand(ui.commandDefinitions.itemById(command_id))
                if command_id == CMD_ID:
                    control.isPromotedByDefault = True
                    control.isPromoted = True


def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        stop(context)
        legacy = ['VEXPerfectMirror_Command_v0' + suffix for suffix in ('11', '12', '13', '14', '15')]
        _remove_controls(ui, legacy)
        for command_id in legacy:
            definition = ui.commandDefinitions.itemById(command_id)
            if definition:
                definition.deleteMe()
        specs = ((CMD_ID, 'Mirror', CMD_DESCRIPTION, _CreatedHandler()),
                 (REPORT_ID, 'Last report', 'Show the last mirror report', _UtilityCreatedHandler(True)),
                 (HELP_ID, 'Help', 'Instructions and supported features', _UtilityCreatedHandler(False)))
        for command_id, name, description, handler in specs:
            definition = ui.commandDefinitions.addButtonDefinition(
                command_id, name, description, RESOURCE_DIR if command_id == CMD_ID else '')
            definition.commandCreated.add(handler)
            _handlers.append(handler)
        _mount_toolbar(ui)
        # The VEX library may start after this add-in. Retry attachment when a
        # workspace or command activates; these callbacks never switch tabs.
        for event_name, handler_name in (('workspaceActivated', 'WorkspaceEventHandler'),
                                          ('commandStarting', 'ApplicationCommandEventHandler')):
            event = getattr(ui, event_name, None)
            handler_class = getattr(adsk.core, handler_name, None)
            if event is not None and handler_class is not None:
                class ToolbarHandler(handler_class):
                    def __init__(self):
                        super().__init__()
                    def notify(self, args):
                        try:
                            _mount_toolbar(adsk.core.Application.get().userInterface)
                        except Exception as ex:
                            _log('VEX toolbar attachment: ' + str(ex))
                handler = ToolbarHandler()
                event.add(handler)
                _toolbar_hooks.append((event, handler))
    except Exception:
        stop(context)
        ui.messageBox('VEX Perfect Mirror could not start:\n' + traceback.format_exc(), CMD_NAME)


def stop(context):
    ui = adsk.core.Application.get().userInterface
    try:
        for event, handler in _toolbar_hooks:
            event.remove(handler)
        _toolbar_hooks.clear()
        _remove_controls(ui, (CMD_ID, REPORT_ID, HELP_ID))
        panel = ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            panel.deleteMe()
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        if workspace:
            tab = workspace.toolbarTabs.itemById(TAB_ID)
            if tab:
                tab.deleteMe()
        for command_id in (CMD_ID, REPORT_ID, HELP_ID):
            definition = ui.commandDefinitions.itemById(command_id)
            if definition:
                definition.deleteMe()
    except Exception as ex:
        _log('Toolbar cleanup: ' + str(ex))
    finally:
        _handlers.clear()
