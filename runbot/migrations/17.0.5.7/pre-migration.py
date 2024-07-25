import logging

from markupsafe import Markup

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""DELETE FROM ir_model_data WHERE module='runbot' AND name = 'docker_base' RETURNING res_id""")
    res_id = cr.fetchone()[0]
    cr.execute("""UPDATE ir_ui_view SET key='runbot.docker_base' WHERE id = %s""", [res_id])
