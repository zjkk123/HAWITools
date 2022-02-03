from odoo import models, fields, api


class OrderWorkflowConfig(models.Model):
    _inherit = "order.workflow.config.ts"

    is_included_fees = fields.Boolean(string='Fee', default=False)
    fee_percent = fields.Float(string="Fee (%)")
    fee_account_id = fields.Many2one('account.account', string='Fee Account', ondelete='restrict', domain="[('deprecated', '=', False)]")