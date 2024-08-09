from collections import ChainMap

from odoo import models
from odoo.tools import ConstantMapping


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _track_set_author(self, author, *, fallback=False):
        """ Set the author of the tracking message. """
        if not self._track_get_fields():
            return
        authors = self.env.cr.precommit.data.setdefault(f'mail.tracking.author.{self._name}', {})
        if fallback:
            details = authors
            if isinstance(authors, ChainMap):
                details = authors.maps[0]
            self.env.cr.precommit.data[f'mail.tracking.author.{self._name}'] = ChainMap(
                details,
                ConstantMapping(author),
            )
        else:
            for id_ in self.ids:
                authors[id_] = author

    def _message_compute_author(self, author_id=None, email_from=None, raise_on_email=True):
        if author_id is None and self and 'id' in self:
            t = self.env.cr.precommit.data.get(f'mail.tracking.author.{self._name}', {})
            if len(authors := {t.get(r.id) for r in self}) == 1:
                author_id = authors.pop()
        return super()._message_compute_author(author_id, email_from, raise_on_email)
