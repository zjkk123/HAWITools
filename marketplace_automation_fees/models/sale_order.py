import logging
from odoo import models, fields, _


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _prepare_payment_vals(self, order_workflow_id, invoice_id, amount=0.0):
        payment_vals = super(SaleOrder, self)._prepare_payment_vals(order_workflow_id, invoice_id, amount=amount)
        if order_workflow_id.is_included_fees and order_workflow_id.fee_percent and order_workflow_id.fee_account_id:
            fees_amount = payment_vals.get('amount') * (order_workflow_id.fee_percent / 100.0)
            amount = payment_vals.get('amount') - fees_amount
            payment_vals.update({
                'amount': amount,
                'write_off_line_vals': {
                    'name': 'Fee for order: {}'.format(self.name),
                    'amount': fees_amount,
                    'account_id': order_workflow_id.fee_account_id.id}})
        return payment_vals
