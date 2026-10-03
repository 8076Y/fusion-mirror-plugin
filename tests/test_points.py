"""Offline regression tests. These mocks do not substitute for a Fusion kernel test."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

class Collection:
    def __init__(self, items=()): self.items = list(items)
    @property
    def count(self): return len(self.items)
    def item(self, i): return self.items[i]
    def add(self, value): self.items.append(value); return value
    def __iter__(self): return iter(self.items)

class Vec:
    def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z
    @staticmethod
    def create(x, y, z): return Vec(x, y, z)
    def copy(self): return Vec(self.x, self.y, self.z)
    def transformBy(self, matrix): return matrix.apply(self)

class Identity:
    def copy(self): return Identity()
    def invert(self): return True
    def apply(self, point): return True

def world(p):
    # A 90-degree rotated and translated sketch/occurrence frame.
    return Vec(10-p.y, -4+p.x, 2+p.z)

def target_local(p):
    # Independent target frame: translation and another 90-degree rotation.
    return Vec(6-p.y, p.x+7, p.z-2)

def target_world(p): return Vec(p.y-7, 6-p.x, 2+p.z)

class Point:
    def __init__(self, pos, sketch, reference=False, connected=False):
        self.geometry, self.sketch = pos, sketch
        self.isReference = reference
        self.isLinked = reference
        self.isFixed = False
        self.isFullyConstrained = False
        self.connectedEntities = Collection([object()] if connected else [])
        self.allow_fix = True
    def __setattr__(self, key, value):
        if key == 'isFixed' and value and not getattr(self, 'allow_fix', True):
            raise RuntimeError('solver rejected Fix')
        object.__setattr__(self, key, value)
    @staticmethod
    def cast(entity): return entity if isinstance(entity, Point) else None
    def createForAssemblyContext(self, occurrence):
        return types.SimpleNamespace(worldGeometry=self.sketch.to_world(self.geometry))
    def deleteMe(self): self.sketch.sketchPoints.items.remove(self); return True

class Points(Collection):
    def __init__(self, sketch): super().__init__(); self.sketch = sketch
    def add(self, pos):
        point = Point(pos, self.sketch)
        self.items.append(point)
        return point

class Sketch:
    def __init__(self, source=False):
        self.name = 'holes'
        self.to_world = world if source else target_world
        self.sketchPoints = Points(self)
        self.originPoint = self.sketchPoints.add(Vec(0,0,0))
        self.originPoint.isReference = True
        self.projection_mode = 'ok'
    def createForAssemblyContext(self, occ):
        return types.SimpleNamespace(modelToSketchSpace=target_local)
    def project2(self, entities, linked):
        if self.projection_mode == 'raise': raise RuntimeError('projection unsupported')
        point = self.sketchPoints.add(entities[0].geometry.copy())
        point.isReference = point.isLinked = linked
        if self.projection_mode == 'wrong': point.geometry.x += 1
        if self.projection_mode == 'unlinked': point.isReference = point.isLinked = False
        return [point]

adsk = types.ModuleType('adsk')
adsk.core = types.ModuleType('adsk.core')
adsk.fusion = types.ModuleType('adsk.fusion')
adsk.core.CommandEventHandler = object
adsk.core.CommandCreatedEventHandler = object
adsk.core.ValidateInputsEventHandler = object
adsk.core.Vector3D = Vec
adsk.core.Point3D = Vec
adsk.fusion.SketchPoint = Point
adsk.log = lambda message: None
sys.modules.update({'adsk': adsk, 'adsk.core': adsk.core, 'adsk.fusion': adsk.fusion})
MODULE_PATH = Path(__file__).resolve().parents[1] / 'VEXPerfectMirror.py'
spec = importlib.util.spec_from_file_location('mirror', MODULE_PATH)
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)

def stats():
    result = {key: 0 for key in ('origins_skipped','reference_points','origin_points',
              'pruned_points','reused_points','reference_fallbacks','projected_points',
              'point_failures','verified_points')}
    result.update(options={'include_origins':False,'prune_margin_cm':0.35},
                  points=[],verification=[],processed_component_pairs=set())
    return result

class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.source, self.target, self.anchor = Sketch(True), Sketch(), Sketch()
        self.occ = types.SimpleNamespace(fullPathName='mechanism:1|bar:2', bRepBodies=Collection(), transform2=Identity())
        self.stats = stats()
    def add(self, reference=False, connected=False, position=None):
        p = self.source.sketchPoints.add(position or Vec(2,3,0))
        p.isReference = p.isLinked = reference
        p.connectedEntities = Collection([object()] if connected else [])
        return p
    def copy(self, prune=False):
        result = mirror._copy_points_world_exact(self.source,self.target,self.anchor,
                                                 self.occ,self.occ,prune,self.stats)
        mirror._verify_points(self.stats)
        return result
    def test_reflection_uses_world_context_and_locks_white_point(self):
        self.add(); self.assertEqual(self.copy(),1)
        p=self.target.sketchPoints.item(1)
        actual=target_world(p.geometry)
        self.assertEqual((actual.x,actual.y,actual.z),(-7,-2,2))
        self.assertTrue(p.isFixed)
        self.assertEqual(self.stats['verified_points'],1)
    def test_null_connected_entities_copies_and_fixes_point(self):
        point = self.add()
        point.connectedEntities = None
        self.copy()
        self.assertEqual(self.stats['point_failures'], 0)
        self.assertEqual(self.stats['verified_points'], 1)
        self.assertTrue(self.target.sketchPoints.item(1).isFixed)
    def test_null_connected_entities_recovers_fixed_nonreference_origin(self):
        self.stats['options']['include_origins'] = True
        origin = self.source.originPoint
        origin.connectedEntities = None
        origin.isReference = False
        origin.isFixed = origin.isFullyConstrained = True
        self.copy()
        self.assertEqual(self.stats['origin_points'], 1)
        self.assertEqual(self.stats['point_failures'], 0)
        self.assertEqual(self.stats['verified_points'], 1)
    def test_null_connected_entities_still_respects_cut_filter(self):
        self.add().connectedEntities = None
        with patch.object(mirror, '_point_near_visible_source_geometry', return_value=False):
            self.copy(prune=True)
        self.assertEqual(self.stats['pruned_points'], 1)
        self.assertEqual(self.stats['point_failures'], 0)
    def test_projected_reference_and_fixed_helper(self):
        self.add(reference=True);self.copy()
        self.assertTrue(self.target.sketchPoints.item(1).isReference)
        self.assertTrue(self.anchor.sketchPoints.item(1).isFixed)
        self.assertEqual(self.stats['projected_points'],1)
    def test_missing_curve_endpoint_is_recovered(self):
        self.add(reference=True,connected=True);self.copy()
        self.assertEqual(self.stats['verified_points'],1)
    def test_existing_curve_point_reused_and_fixed(self):
        src=self.add(connected=True)
        pos=target_local(mirror._mirror_point_yz(world(src.geometry)))
        dest=self.target.sketchPoints.add(pos)
        self.assertEqual(self.copy(),0)
        self.assertTrue(dest.isFixed)
        self.assertEqual(self.target.sketchPoints.count,2)
    def test_origins_are_recovered_without_reusing_invisible_target_origin(self):
        self.stats['options']['include_origins']=True
        self.copy()
        self.assertEqual(self.stats['origin_points'],1)
        self.assertEqual(self.target.sketchPoints.count,2)
    def test_origin_option_can_be_disabled(self):
        self.copy();self.assertEqual(self.stats['origins_skipped'],1)
        self.assertEqual(self.target.sketchPoints.count,1)
    def test_projection_unavailable_keeps_fixed_position(self):
        self.add(reference=True);self.target.projection_mode='raise';self.copy()
        self.assertTrue(self.target.sketchPoints.item(1).isFixed)
        self.assertEqual(self.stats['reference_fallbacks'],1)
        self.assertEqual(self.stats['point_failures'],0)
    def test_wrong_projection_is_removed_before_fallback(self):
        self.add(reference=True);self.target.projection_mode='wrong';self.copy()
        self.assertEqual(self.target.sketchPoints.count,2)
        self.assertEqual(self.stats['reference_fallbacks'],1)
    def test_unlinked_projection_is_removed_before_fallback(self):
        self.add(reference=True);self.target.projection_mode='unlinked';self.copy()
        self.assertEqual(self.target.sketchPoints.count,2)
        self.assertTrue(self.target.sketchPoints.item(1).isFixed)
    def test_nonplanar_reference_not_flattened(self):
        self.add(reference=True,position=Vec(2,3,4));self.copy()
        self.assertEqual(self.target.sketchPoints.item(1).geometry.z,4)
        self.assertEqual(self.stats['reference_fallbacks'],1)
    def test_same_location_deduplicated(self):
        self.add();self.add();self.copy()
        self.assertEqual(self.target.sketchPoints.count,2)
        self.assertEqual(self.stats['reused_points'],1)
    def test_post_compute_movement_is_detected(self):
        self.add();self.copy()
        p=self.target.sketchPoints.item(1)
        self.stats['verification']=[(p,self.occ,Vec(-7,-2,2),{})]
        p.geometry.x+=1
        mirror._verify_points(self.stats)
        self.assertEqual(self.stats['point_failures'],1)
    def test_fix_failure_is_not_reported_as_success(self):
        src=self.add();dest=self.target.sketchPoints.add(target_local(mirror._mirror_point_yz(world(src.geometry))))
        dest.allow_fix=False
        self.copy();self.assertEqual(self.stats['point_failures'],1)
        self.assertEqual(self.stats['verified_points'],0)
    def test_cut_filter_ignores_hidden_stock(self):
        box=lambda low,high:types.SimpleNamespace(minPoint=Vec(*low),maxPoint=Vec(*high))
        visible=types.SimpleNamespace(isLightBulbOn=True,preciseBoundingBox=box((5,-3,1),(8,0,3)))
        hidden=types.SimpleNamespace(isLightBulbOn=False,preciseBoundingBox=box((-40,-3,1),(4,0,3)))
        self.occ.bRepBodies=Collection([visible,hidden])
        self.add();self.add(position=Vec(2,30,0));self.copy(prune=True)
        self.assertEqual(self.stats['pruned_points'],1)
        self.assertEqual(self.stats['verified_points'],1)
    def test_tolerance_changes_cut_filter(self):
        box=types.SimpleNamespace(minPoint=Vec(0,0,0),maxPoint=Vec(1,1,1))
        self.assertFalse(mirror._point_inside_box(Vec(1.2,0,0),box,0.1))
        self.assertTrue(mirror._point_inside_box(Vec(1.2,0,0),box,0.35))
    def test_pruning_does_not_remove_structural_curve_point(self):
        self.add(connected=True)
        with patch.object(mirror,'_point_near_visible_source_geometry',return_value=False):self.copy(prune=True)
        self.assertEqual(self.stats['verified_points'],1)
    def test_report_is_serializable_and_contains_outcomes(self):
        self.add(reference=True);self.copy()
        with tempfile.TemporaryDirectory() as directory:
            mkstemp=tempfile.mkstemp
            with patch.object(mirror.tempfile,'mkstemp',side_effect=lambda **kw:mkstemp(dir=directory,**kw)):
                mirror._save_report(self.stats)
            report=json.loads(Path(self.stats['report_path']).read_text())
        self.assertEqual(report['version'],'1.0.4')
        self.assertEqual(report['points'][0]['status'],'verified')
        self.assertNotIn('verification',report)
    def test_reflected_body_pairing_restores_hidden_state(self):
        def body(x,state):
            box=types.SimpleNamespace(minPoint=Vec(x-1,0,0),maxPoint=Vec(x+1,1,1))
            return types.SimpleNamespace(isLightBulbOn=state,preciseBoundingBox=box)
        a,b=body(4,True),body(10,False)
        c,d=body(-10,True),body(-4,False)
        n=mirror._restore_body_visibility(types.SimpleNamespace(bRepBodies=Collection([a,b])),
                                         types.SimpleNamespace(bRepBodies=Collection([c,d])))
        self.assertEqual(n,2);self.assertFalse(c.isLightBulbOn);self.assertTrue(d.isLightBulbOn)

if __name__=='__main__':unittest.main()
