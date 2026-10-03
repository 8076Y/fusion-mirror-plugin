"""Release regression tests, including command/UI lifecycle and mocked execution."""
import json
import math
from pathlib import Path
import random
import tempfile
import types
import unittest
from unittest.mock import patch
from test_points import mirror, adsk, Vec, Collection, Identity

NS = types.SimpleNamespace

class ObjectCollection(Collection):
    @staticmethod
    def create(): return ObjectCollection()

adsk.core.ObjectCollection = ObjectCollection
adsk.core.Plane = NS(cast=lambda geometry: geometry if hasattr(geometry, 'normal') else None)
adsk.fusion.JointTypes = NS(RigidJointType=0)
adsk.fusion.Occurrence = NS(cast=lambda obj: obj)
adsk.fusion.Design = NS(cast=lambda obj: obj)
adsk.doEvents = lambda: None

def xyz(point): return (point.x, point.y, point.z)

class GeometryTests(unittest.TestCase):
    def test_three_origin_planes(self):
        for normal, wanted in [((1,0,0),(-2,3,4)), ((0,1,0),(2,-3,4)), ((0,0,1),(2,3,-4))]:
            self.assertEqual(xyz(mirror.Reflection(normal=normal).point(Vec(2,3,4))), wanted)
    def test_offset_plane(self):
        self.assertEqual(xyz(mirror.Reflection((5,0,0),(1,0,0)).point(Vec(8,2,3))), (2,2,3))
    def test_oblique_plane_and_involution_randomized(self):
        rng = random.Random(14)
        for _ in range(200):
            origin=tuple(rng.uniform(-30,30) for _ in range(3))
            normal=tuple(rng.uniform(-1,1) for _ in range(3))
            reflection=mirror.Reflection(origin,normal)
            point=Vec(*(rng.uniform(-100,100) for _ in range(3)))
            mirrored=reflection.point(point)
            recovered=reflection.point(mirrored)
            for x,y in zip(xyz(point),xyz(recovered)): self.assertAlmostEqual(x,y,places=10)
            midpoint=tuple((a+b)/2 for a,b in zip(xyz(point),xyz(mirrored)))
            self.assertAlmostEqual(sum((m-o)*n for m,o,n in zip(midpoint,origin,reflection.normal)),0,places=10)
    def test_vector_does_not_inherit_plane_offset(self):
        reflection=mirror.Reflection((12,9,2),(1,0,0))
        self.assertEqual(xyz(reflection.vector(Vec(2,3,4))),(-2,3,4))
    def test_bad_normals_and_nonfinite_values_rejected(self):
        for origin,normal in [((0,0,0),(0,0,0)),((math.nan,0,0),(1,0,0)),((0,0,0),(math.inf,0,0))]:
            with self.assertRaises(ValueError):mirror.Reflection(origin,normal)
    def test_plane_in_component_context_transformed_once(self):
        class Transform(Identity):
            def apply(self, point):
                point.x,point.y=-point.y,point.x
                return True
        root=object()
        native=NS(geometry=NS(origin=Vec(1,0,0),normal=Vec(1,0,0)),parentComponent=object())
        plane=NS(nativeObject=native,assemblyContext=NS(transform2=NS(copy=lambda:Transform())))
        reflection=mirror._reflection_for_plane(plane,root)
        self.assertEqual(reflection.origin,(0,1,0))
        self.assertEqual(reflection.normal,(0,1,0))
    def test_native_child_plane_without_occurrence_is_rejected(self):
        plane=NS(geometry=NS(origin=Vec(0,0,0),normal=Vec(1,0,0)),parentComponent=object())
        with self.assertRaises(ValueError):mirror._reflection_for_plane(plane,object())
    def test_ambiguous_matching_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'ambiguous'):
            mirror._pair_by_reflected_centers([Vec(1,0,0),Vec(1,0,0)],
                                             [Vec(-1,0,0),Vec(-1,0,0)],lambda o:o,strict=True)
    def test_body_count_mismatch_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'counts'):
            mirror._pair_by_reflected_centers([Vec(1,0,0)],[],lambda o:o,strict=True)
    def test_wrong_location_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'position'):
            mirror._pair_by_reflected_centers([Vec(1,0,0)],[Vec(2,0,0)],lambda o:o,strict=True)
    def test_point_filter_in_rotated_component_coordinates(self):
        class RotatedInverse(Identity):
            def invert(self):return True
            def apply(self,p):p.x,p.y=p.y,-p.x;return True
        box=NS(minPoint=Vec(0,0,0),maxPoint=Vec(10,1,1))
        native=NS(preciseBoundingBox=box,isLightBulbOn=True)
        body=NS(nativeObject=native)
        hidden=NS(isLightBulbOn=False)
        occurrence=NS(bRepBodies=Collection([body,hidden]),transform2=NS(copy=lambda:RotatedInverse()))
        self.assertTrue(mirror._point_near_visible_source_geometry(occurrence,Vec(-0.5,5,0.5),0))
        self.assertFalse(mirror._point_near_visible_source_geometry(occurrence,Vec(-0.5,20,0.5),0))
    def test_uncut_components_keep_construction_points(self):
        occurrence=NS(bRepBodies=Collection([NS(isLightBulbOn=True)]))
        self.assertTrue(mirror._point_near_visible_source_geometry(occurrence,Vec(100,100,100)))

class Event:
    def __init__(self):self.handlers=[]
    def add(self,h):self.handlers.append(h);return True
class Selection:
    def __init__(self):self.selected=[];self.filters=[];self.limits=None;self.created=True
    def clearSelection(self):self.selected=[];return True
    def addSelectionFilter(self,f):self.filters.append(f);return True
    def setSelectionLimits(self,*limits):self.limits=limits
    def addSelection(self,o):
        if self.created:raise RuntimeError('addSelection forbidden during commandCreated')
        self.selected.append(o);return True
    @property
    def selectionCount(self):return len(self.selected)
    def selection(self,i):return NS(entity=self.selected[i])
class Inputs:
    def __init__(self):self.items={}
    def addSelectionInput(self,key,*args):self.items[key]=Selection();return self.items[key]
    def itemById(self,key):return self.items[key]
class Progress:
    wasCancelled=False
    def show(self,*a):pass
    def hide(self):pass
class Command:
    def __init__(self):
        self.commandInputs=Inputs()
        self.execute,self.validateInputs,self.activate,self.destroy=[Event() for _ in range(4)]

def app_context(design=None):
    messages=[]
    app=NS(userInterface=NS(messageBox=lambda *args:messages.append(args),createProgressDialog=Progress),
           activeProduct=design,activeEditObject=None)
    return app,messages

class UiTests(unittest.TestCase):
    def setUp(self):
        self.app,self.messages=app_context(NS(rootComponent=NS(yZConstructionPlane=object())))
        self.patcher=patch.object(adsk.core,'Application',NS(get=lambda:self.app),create=True);self.patcher.start()
        self.command=Command()
        mirror._handlers.clear()
    def tearDown(self):self.patcher.stop();mirror._handlers.clear()
    def test_two_inputs_start_empty_and_later_choices_preserved(self):
        mirror._CreatedHandler().notify(NS(command=self.command))
        self.assertFalse(self.messages)
        self.assertEqual(set(self.command.commandInputs.items),{'occurrences','plane'})
        plane=self.command.commandInputs.itemById('plane')
        components=self.command.commandInputs.itemById('occurrences')
        plane.selected=[object()];components.selected=[object()]
        event=NS(firingEvent=NS(sender=self.command))
        self.command.activate.handlers[0].notify(event)
        self.assertEqual(plane.selectionCount,0)
        self.assertEqual(components.selectionCount,0)
        plane.selected=[object()];components.selected=[object()]
        self.command.activate.handlers[0].notify(event)
        self.assertEqual(plane.selectionCount,1)
        self.assertEqual(components.selectionCount,1)
    def test_validate_requires_components_and_plane(self):
        mirror._CreatedHandler().notify(NS(command=self.command))
        args=NS(inputs=self.command.commandInputs)
        mirror._ValidateHandler().notify(args);self.assertFalse(args.areInputsValid)
        args.inputs.itemById('plane').selected=[object()]
        args.inputs.itemById('occurrences').selected=[object()]
        mirror._ValidateHandler().notify(args);self.assertTrue(args.areInputsValid)
    def test_execute_failure_requests_transaction_abort(self):
        mirror._CreatedHandler().notify(NS(command=self.command))
        for item in self.command.commandInputs.items.values():item.selected=[object()]
        args=NS(firingEvent=NS(sender=self.command),executeFailed=False)
        with patch.object(mirror,'_normalize_selection',return_value=[object()]), patch.object(mirror,'_mirror_selected_occurrences',side_effect=RuntimeError('bad geometry')):
            mirror._ExecuteHandler().notify(args)
        self.assertTrue(args.executeFailed)
        self.assertIn('bad geometry',args.executeFailedMessage)
        self.assertFalse(mirror._running)
    def test_cancel_requests_transaction_abort(self):
        mirror._CreatedHandler().notify(NS(command=self.command))
        for item in self.command.commandInputs.items.values():item.selected=[object()]
        args=NS(firingEvent=NS(sender=self.command),executeFailed=False)
        with patch.object(mirror,'_normalize_selection',return_value=[object()]), patch.object(mirror,'_mirror_selected_occurrences',side_effect=mirror._Cancelled('cancelled')):
            mirror._ExecuteHandler().notify(args)
        self.assertTrue(args.executeFailed)
    def test_command_handlers_released_on_destroy(self):
        mirror._CreatedHandler().notify(NS(command=self.command))
        self.assertEqual(len(mirror._handlers),4)
        self.command.destroy.handlers[0].notify(None)
        self.assertEqual(len(mirror._handlers),0)
    def test_manifest_enables_startup(self):
        manifest=json.loads((Path(__file__).parents[1]/'VEXPerfectMirror.manifest').read_text())
        self.assertTrue(manifest['runOnStartup']);self.assertEqual(manifest['version'],mirror.VERSION)

class Component:
    def __init__(self,name):
        self.name=name;self.meshBodies=Collection();self.sketches=Collection()
        self.description='VEX';self.partNumber='sample';self.isBodiesFolderLightBulbOn=True
        self.isSketchFolderLightBulbOn=True
class Occurrence:
    def __init__(self,name,centers,reflection=None):
        self.name=self.fullPathName=name
        self.isValid=True;self.assemblyContext=None;self.component=Component(name)
        self.childOccurrences=Collection();self.isGrounded=False;self.isLightBulbOn=True
        self.bRepBodies=Collection()
        for i,center in enumerate(centers):
            p=reflection.point(center) if reflection else center.copy()
            self.bRepBodies.add(NS(physicalProperties=NS(centerOfMass=p,volume=1.),
                                  isLightBulbOn=i==0,faces=Collection([1]),edges=Collection([1]),isSolid=True))
    def activate(self):return True

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.source=Occurrence('bar:1',[Vec(5,1,2),Vec(8,1,2)])
        self.root=NS(occurrences=Collection([self.source]),allOccurrences=Collection([self.source]),
                     allJoints=[],allAsBuiltJoints=[],allRigidGroups=[])
        self.reflection=mirror.Reflection((2,0,0),(1,1,0))
        self.plane=NS(geometry=NS(origin=Vec(2,0,0),normal=Vec(1,1,0)),parentComponent=self.root)
        def add(data):
            src=data.entities.item(0)
            output=Occurrence(src.name+' mirror',[b.physicalProperties.centerOfMass for b in src.bRepBodies],self.reflection)
            self.root.occurrences.add(output)
            return NS(name='mirror')
        self.root.features=NS(mirrorFeatures=NS(createInput=lambda entities,plane:NS(entities=entities,plane=plane),add=add))
        self.design=NS(rootComponent=self.root,activeOccurrence=None,activateRootComponent=lambda:True,
                       computeAll=lambda:True,designType=0)
        self.app,_=app_context(self.design)
        self.patcher=patch.object(adsk.core,'Application',NS(get=lambda:self.app),create=True);self.patcher.start()
        adsk.fusion.Component=NS(cast=lambda obj:obj if isinstance(obj,Component) else None)
    def tearDown(self):self.patcher.stop()
    def test_end_to_end_plane_visibility_metadata_and_grounding(self):
        self.source.isGrounded=True
        with patch.object(mirror,'_save_report'):
            stats=mirror._mirror_selected_occurrences([self.source],self.plane)
        self.assertEqual(stats['status'],'completed')
        output=self.root.occurrences.item(1)
        self.assertTrue(output.bRepBodies.item(0).isLightBulbOn)
        self.assertFalse(output.bRepBodies.item(1).isLightBulbOn)
        self.assertTrue(output.isGrounded)
        self.assertEqual(output.component.partNumber,self.source.component.partNumber)
        self.assertEqual(self.source.component.name,'bar:1')
        self.assertEqual(stats['body_visibility'],2)
    def test_component_edit_target_is_allowed(self):
        self.app.activeEditObject=self.source.component
        with patch.object(mirror,'_save_report'):
            self.assertEqual(mirror._mirror_selected_occurrences([self.source],self.plane)['status'],'completed')
    def test_sketch_edit_target_is_rejected(self):
        self.app.activeEditObject=object()
        with self.assertRaisesRegex(ValueError,'Finish editing'):
            mirror._mirror_selected_occurrences([self.source],self.plane)
        self.assertEqual(self.root.occurrences.count,1)
    def test_point_failures_reject_operation_and_save_failed_report(self):
        def fail(*args):args[-1]['point_failures']+=1
        with patch.object(mirror,'_copy_sketches_for_component_pair',side_effect=fail),patch.object(mirror,'_save_report') as save:
            with self.assertRaisesRegex(RuntimeError,'not accepted'):
                mirror._mirror_selected_occurrences([self.source],self.plane)
            self.assertEqual(save.call_args.args[0]['status'],'failed')
    def test_duplicate_selection_normalized(self):
        self.assertEqual(len(mirror._normalize_selection([self.source,self.source],self.root)),1)
    def test_nested_selection_accepted(self):
        self.source.assemblyContext=object()
        self.assertEqual(mirror._normalize_selection([self.source],self.root),[self.source])
    def test_mesh_rejected_before_mutation(self):
        self.source.component.meshBodies.add(object())
        with self.assertRaisesRegex(ValueError,'Mesh'):
            mirror._mirror_selected_occurrences([self.source],self.plane)
        self.assertEqual(self.root.occurrences.count,1)

class RelationshipTests(unittest.TestCase):
    def setUp(self):
        self.a,self.b=NS(fullPathName='a'),NS(fullPathName='b')
        self.x,self.y=NS(fullPathName='x'),NS(fullPathName='y')
        self.stats=mirror._new_stats(mirror.Reflection(),None)
        self.stats['occurrence_pairs']=[(self.a,self.x),(self.b,self.y)]
        self.created=[]
        def create(one,two,geometry):return NS(one=one,two=two,setAsRigidJointMotion=lambda:True)
        def add(data):
            joint=NS(occurrenceOne=data.one,occurrenceTwo=data.two,jointMotion=NS(jointType=0),isSuppressed=False)
            self.created.append(joint);return joint
        self.root=NS(allJoints=[],allAsBuiltJoints=self.created,allRigidGroups=[],
                     asBuiltJoints=NS(createInput=create,add=add))
    def joint(self,kind=0):return NS(name='connection',occurrenceOne=self.a,occurrenceTwo=self.b,
                                   jointMotion=NS(jointType=kind),isSuppressed=False,isLightBulbOn=False)
    def test_internal_rigid_joint_recreated_without_geometry(self):
        mirror._copy_rigid_relationships(self.root,[self.joint()],[],self.stats)
        self.assertEqual(self.stats['rigid_joints'],1)
        self.assertEqual(len(self.created),1)
        self.assertIs(self.created[0].occurrenceOne,self.x)
    def test_moving_joint_explicitly_omitted(self):
        mirror._copy_rigid_relationships(self.root,[self.joint(1)],[],self.stats)
        self.assertEqual(len(self.stats['relationships_omitted']),1)
        self.assertEqual(len(self.created),0)
    def test_external_connection_explicitly_omitted(self):
        joint=self.joint();joint.occurrenceTwo=NS(fullPathName='outside')
        mirror._copy_rigid_relationships(self.root,[joint],[],self.stats)
        self.assertIn('unselected',self.stats['relationships_omitted'][0]['reason'])
    def test_existing_native_rigid_joint_not_duplicated(self):
        existing=self.joint();existing.occurrenceOne=self.x;existing.occurrenceTwo=self.y
        self.created.append(existing)
        mirror._copy_rigid_relationships(self.root,[self.joint()],[],self.stats)
        self.assertEqual(len(self.created),1)
    def test_internal_rigid_group_recreated(self):
        groups=[]
        self.root.rigidGroups=NS(add=lambda members,children:groups.append(NS(occurrences=members)) or groups[-1])
        source=NS(name='group',occurrences=Collection([self.a,self.b]),isSuppressed=False)
        mirror._copy_rigid_relationships(self.root,[],[source],self.stats)
        self.assertEqual(self.stats['rigid_groups'],1)
        self.assertEqual(groups[0].occurrences.items,[self.x,self.y])
    def test_cancel_checked_before_relationship_creation(self):
        self.stats['progress']=NS(wasCancelled=True)
        with self.assertRaises(mirror._Cancelled):
            mirror._copy_rigid_relationships(self.root,[self.joint()],[],self.stats)
        self.assertFalse(self.created)

    def test_joint_snapshot_survives_source_getter_failure(self):
        original = self.joint()
        saved = mirror._relationship_snapshot(original)
        class Invalidated:
            name = 'stale as-built joint'
            isSuppressed = False
            @property
            def occurrenceOne(self): raise RuntimeError('2 : InternalValidationError : res')
        self.root.allJoints = [Invalidated()]
        mirror._copy_rigid_relationships(self.root,[saved],[],self.stats)
        self.assertEqual(self.stats['rigid_joints'],1)
        self.assertEqual(self.created[0].occurrenceOne,self.x)
    def test_unreadable_source_joint_is_reported(self):
        class Unreadable:
            name = 'broken joint'
            isSuppressed = False
            @property
            def occurrenceOne(self): raise RuntimeError('InternalValidationError')
        saved = mirror._relationship_snapshot(Unreadable())
        mirror._copy_rigid_relationships(self.root,[saved],[],self.stats)
        self.assertEqual(self.created,[])
        self.assertIn('InternalValidationError',self.stats['relationships_omitted'][0]['reason'])
    def test_group_snapshot_survives_source_member_change(self):
        groups=[]
        self.root.rigidGroups=NS(add=lambda members,children:groups.append(NS(occurrences=members)) or groups[-1])
        original=NS(name='group',occurrences=Collection([self.a,self.b]),isSuppressed=False)
        saved=mirror._relationship_snapshot(original,group=True)
        original.occurrences=Collection([])
        mirror._copy_rigid_relationships(self.root,[],[saved],self.stats)
        self.assertEqual(groups[0].occurrences.items,[self.x,self.y])

class SelectionHotfixTests(unittest.TestCase):
    def setUp(self):
        self.source = Occurrence('left:1', [])
        self.root = Component('lift')
        self.root.occurrences = Collection([self.source])
        self.occ_cast = patch.object(adsk.fusion, 'Occurrence', NS(cast=lambda e: e if isinstance(e, Occurrence) else None))
        self.comp_cast = patch.object(adsk.fusion, 'Component', NS(cast=lambda e: e if isinstance(e, Component) else None), create=True)
        self.occ_cast.start(); self.comp_cast.start()
    def tearDown(self):
        self.comp_cast.stop(); self.occ_cast.stop()
    def test_assembly_occurrence_and_duplicates(self):
        self.assertEqual(mirror._normalize_selection([self.source, self.source], self.root), [self.source])
    def test_component_resolves_unique_instance(self):
        self.assertEqual(mirror._normalize_selection([self.source.component], self.root), [self.source])
    def test_repeated_component_is_not_guessed(self):
        other = Occurrence('left:2', [])
        other.component = self.source.component
        self.root.occurrences.add(other)
        with self.assertRaisesRegex(ValueError, '2 assembly instances'):
            mirror._normalize_selection([self.source.component], self.root)
    def test_root_rejected(self):
        with self.assertRaisesRegex(ValueError, 'document root'):
            mirror._normalize_selection([self.root], self.root)
    def test_wrong_type_identified(self):
        with self.assertRaisesRegex(ValueError, 'adsk::fusion::BRepBody'):
            mirror._normalize_selection([NS(name='body', objectType='adsk::fusion::BRepBody')], self.root)
    def test_stale_distinguished(self):
        self.source.isValid = False
        with self.assertRaisesRegex(ValueError, 'stale: left:1'):
            mirror._normalize_selection([self.source], self.root)
    def test_nested_not_promoted(self):
        parent=Occurrence('parent:1', [])
        self.source.fullPathName='parent:1+bar:1'
        self.source.assemblyContext=parent
        self.root.occurrences=Collection([parent])
        self.root.allOccurrences=Collection([parent,self.source])
        self.assertEqual(mirror._normalize_selection([self.source],self.root),[self.source])
    def test_parent_and_child_not_copied_twice(self):
        parent=Occurrence('parent:1', [])
        self.source.fullPathName='parent:1+bar:1'
        self.root.allOccurrences=Collection([parent,self.source])
        self.assertEqual(mirror._normalize_selection([self.source,parent],self.root),[parent])
    def test_several_instances_in_populated_design(self):
        other=Occurrence('other:1', [])
        unrelated=Occurrence('unselected:1', [])
        self.root.allOccurrences=Collection([self.source,other,unrelated])
        self.assertEqual(mirror._normalize_selection([self.source,other],self.root),[self.source,other])
    def test_execute_preserves_raw_selection(self):
        command = Command()
        command.commandInputs.addSelectionInput('occurrences').selected = [self.source.component]
        command.commandInputs.addSelectionInput('plane').selected = [object()]
        app, _ = app_context(NS(rootComponent=self.root))
        args = NS(firingEvent=NS(sender=command), executeFailed=False)
        with patch.object(adsk.core, 'Application', NS(get=lambda:app), create=True), patch.object(mirror, '_mirror_selected_occurrences', side_effect=RuntimeError('stop after selection')) as run:
            mirror._ExecuteHandler().notify(args)
        self.assertEqual(run.call_args.args[0], [self.source])
        self.assertIn('stop after selection', args.executeFailedMessage)

class CutBodyHotfixTests(unittest.TestCase):
    def setUp(self):
        self.source = Occurrence('cut bar:1', [Vec(5,0,0), Vec(8,0,0)])
        self.target = Occurrence('mirror:1', [Vec(-5,0,0)])
        self.reflection = mirror.Reflection()
    def test_hidden_offcut_can_be_omitted(self):
        self.source.physicalProperties = NS(volume=2)
        self.target.physicalProperties = NS(volume=1)
        self.assertEqual(mirror._collect_occurrence_pairs(self.source,self.target,self.reflection), [(self.source,self.target)])
        self.assertEqual(mirror._restore_body_visibility(self.source,self.target,self.reflection,True),1)
    def test_present_hidden_body_restored(self):
        self.target.bRepBodies.add(NS(physicalProperties=NS(centerOfMass=Vec(-8,0,0),volume=1.),isLightBulbOn=True,faces=Collection([1]),edges=Collection([1]),isSolid=True))
        self.assertEqual(mirror._restore_body_visibility(self.source,self.target,self.reflection,True),2)
        self.assertFalse(self.target.bRepBodies.item(1).isLightBulbOn)
    def test_missing_visible_stock_rejected(self):
        self.source.bRepBodies.item(1).isLightBulbOn = True
        with self.assertRaisesRegex(RuntimeError,'Visible body missing'):
            mirror._collect_occurrence_pairs(self.source,self.target,self.reflection)
    def test_wrong_position_rejected(self):
        self.target.bRepBodies.item(0).physicalProperties.centerOfMass = Vec(5,0,0)
        with self.assertRaisesRegex(RuntimeError,'Visible body missing'):
            mirror._collect_occurrence_pairs(self.source,self.target,self.reflection)
    def test_extra_output_rejected(self):
        self.target.bRepBodies.add(NS(physicalProperties=NS(centerOfMass=Vec(-10,0,0),volume=1.),isLightBulbOn=True,faces=Collection([1]),edges=Collection([1]),isSolid=True))
        with self.assertRaisesRegex(RuntimeError,'Unmatched output'):
            mirror._collect_occurrence_pairs(self.source,self.target,self.reflection)
    def test_nested_cut_bar_matched_without_aggregate_centroid(self):
        a,b = Occurrence('assembly:1',[]),Occurrence('assembly mirror:1',[])
        a.childOccurrences.add(self.source); b.childOccurrences.add(self.target)
        self.assertEqual(len(mirror._collect_occurrence_pairs(a,b,self.reflection)),2)

class ToolbarUpdateTests(unittest.TestCase):
    def test_mounts_existing_vex_tab_and_fallback_without_new_tab(self):
        class Controls:
            def __init__(self):self.items={}
            def itemById(self,key):return self.items.get(key)
            def addCommand(self,definition):
                control=NS();self.items[definition.id]=control;return control
        class Panels:
            def __init__(self):self.items={}
            def itemById(self,key):return self.items.get(key)
            def add(self,key,name):
                panel=NS(controls=Controls());self.items[key]=panel;return panel
        vex=NS(name='VEX CAD LIBRARY',toolbarPanels=Panels())
        solid=NS(controls=Controls())
        tabs=[vex]
        workspace=NS(toolbarTabs=tabs)
        ui=NS(workspaces=NS(itemById=lambda key:workspace),
              allToolbarPanels=NS(itemById=lambda key:solid if key=='SolidCreatePanel' else None),
              commandDefinitions=NS(itemById=lambda key:NS(id=key)))
        mirror._mount_toolbar(ui);mirror._mount_toolbar(ui)
        self.assertEqual(len(tabs),1)
        self.assertEqual(len(vex.toolbarPanels.itemById(mirror.PANEL_ID).controls.items),3)
        self.assertIn(mirror.CMD_ID,solid.controls.items)
        tabs.clear()
        mirror._mount_toolbar(ui)
        self.assertEqual(tabs,[])
    def test_summary_is_two_lines_with_omission_count(self):
        stats=mirror._new_stats(mirror.Reflection(),None)
        stats['relationships_omitted']=[{},{}]
        self.assertEqual(mirror._result_message(stats),'Copy successful.\n2 relationships could not be copied over.')

if __name__=='__main__':unittest.main()
