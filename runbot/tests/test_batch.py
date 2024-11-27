from datetime import timedelta

from odoo import fields

from .common import RunbotCase


class TestBatch(RunbotCase):

    def test_process_delay(self):
        self.project.process_delay = 120
        self.additionnal_setup()

        batch = self.branch_addons.bundle_id.last_batch
        batch._process()
        self.assertEqual(batch.state, 'preparing')

        batch.last_update = fields.Datetime.now() - timedelta(seconds=120)
        batch._process()
        self.assertEqual(batch.state, 'ready')
