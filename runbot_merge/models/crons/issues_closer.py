import logging

from odoo import models, fields

_logger = logging.getLogger(__name__)
class BranchCleanup(models.Model):
    _name = 'runbot_merge.issues_closer'
    _description = "closes issues linked to PRs"

    repository_id = fields.Many2one('runbot_merge.repository', required=True)
    number = fields.Integer(required=True)

    def _run(self):
        ghs = {}
        while t := self.search([], limit=1):
            gh = ghs.get(t.repository_id.id)
            if not gh:
                gh = ghs[t.repository_id.id] = t.repository_id.github()

            r = gh('PATCH', f'issues/{t.number}', json={'state': 'closed'}, check=False)
            t.unlink()
