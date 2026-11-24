import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from backend.image_editor import ImageSessionStore


class ReferenceReviewStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ImageSessionStore(self.root / 'pictures')

    def image(self, name, color):
        file = self.root / (name + '.jpg')
        Image.new('RGB', (32, 24), color).save(file)
        return file

    def test_inspected_photos_survive_history_without_becoming_image_inputs(self):
        self.store.begin_round('workspace', 'root', 'Product study', 1, 0, {})
        records = [{'id': 'photo-' + str(index), 'path': str(self.image(str(index), color)), 'title': color}
                   for index, color in enumerate(('red', 'blue'))]
        saved = self.store.persist_web_references('workspace', 'root', records, inspected_only=True)
        self.assertEqual(len(saved), 2)
        restored = self.store.list_sets()[0]
        self.assertEqual(len(restored['webInspectedReferences']), 2)
        self.assertEqual(restored['webReferences'], [])
        self.assertEqual(restored['webReferenceCount'], 0)
        self.assertTrue(all(Path(item['path']).is_file() for item in restored['webInspectedReferences']))
        context = self.store.continuation_context('workspace', 'root', include_workspace=True)
        self.assertEqual(len(context['assets']), 2)
        self.assertTrue(all(item['sourceRoles'] == ['web-inspected'] for item in context['assets']))
        self.assertEqual(context['history'][0]['inputAssetIds'], [])
        for item in saved:
            self.assertTrue(Path(item['path']).is_relative_to(context['workspaceRoot']))

    def test_only_selected_photo_is_recorded_as_final_input(self):
        self.store.begin_round('workspace', 'root', 'Product study', 1, 0, {})
        records = [{'id': 'photo-' + str(index), 'path': str(self.image(str(index), color))}
                   for index, color in enumerate(('red', 'blue'))]
        inspected = self.store.persist_web_references('workspace', 'root', records, inspected_only=True)
        adopted = self.store.persist_web_references('workspace', 'root', records[:1])
        restored = self.store.list_sets()[0]
        self.assertEqual(len(restored['webInspectedReferences']), 2)
        self.assertEqual(len(restored['webReferences']), 1)
        context = self.store.continuation_context('workspace', 'root', include_workspace=True)
        self.assertEqual(context['history'][0]['inputAssetIds'], [adopted[0]['assetId']])
        self.assertNotIn(inspected[1]['assetId'], context['history'][0]['inputAssetIds'])

    def test_workspace_history_includes_siblings_but_parent_remains_explicit(self):
        for name, parent, color in [('root', '', 'red'), ('a', 'root', 'green'), ('b', 'root', 'blue')]:
            self.store.begin_round('workspace', name, name, 1, 0, {}, parent_set_id=parent)
            self.store.persist_result('workspace', name, 0, self.image(name, color),
                                      {'width': 32, 'height': 24, 'format': 'jpeg'})
            self.store.complete_round('workspace', name)
        branch = self.store.continuation_context('workspace', 'a')
        self.assertEqual([item['setId'] for item in branch['history']], ['root', 'a'])
        workspace = self.store.continuation_context('workspace', 'a', include_workspace=True)
        self.assertEqual([item['setId'] for item in workspace['history']], ['root', 'a', 'b'])
        self.assertEqual([item['setId'] for item in workspace['history'] if item['activeBranch']], ['root', 'a'])
        self.assertEqual(workspace['parentOutputAssetIds'], workspace['history'][1]['outputAssetIds'])
        self.assertEqual(workspace['parentSetId'], 'a')
        self.assertTrue(all(Path(item['path']).is_relative_to(workspace['workspaceRoot']) for item in workspace['assets']))
        self.assertEqual(self.store.continuation_context('workspace', 'a', ['foreign'], include_workspace=True)['requiredInputAssetIds'], [])


if __name__ == '__main__':
    unittest.main()
