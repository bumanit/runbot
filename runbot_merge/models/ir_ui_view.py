from odoo import models


class View(models.Model):
    _inherit = 'ir.ui.view'

    def _log_view_warning(self, msg, node):
        """The view validator is dumb and triggers a warning because there's a
        `field.btn`, even though making a `field[widget=url]` (which renders as
        a link) look like a button is perfectly legitimate.

        Suppress that warning.
        """
        if node.tag == 'field' and node.get('widget') == 'url' and "button/submit/reset" in msg:
            return

        super()._log_view_warning(msg, node)
