from odoo.upgrade import util

def migrate(cr, _version):
    util.remove_field(cr, "res.partner", "message_main_attachment_id")
    util.remove_field(cr, "runbot_merge.batch", "message_main_attachment_id")
    util.remove_field(cr, "runbot_merge.patch", "message_main_attachment_id")
    util.remove_field(cr, "runbot_merge.pull_requests", "message_main_attachment_id")
    util.remove_field(cr, "runbot_merge.pull_requests.feedback.template", "message_main_attachment_id")
