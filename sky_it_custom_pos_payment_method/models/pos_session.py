# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models, _

class PosSession(models.Model):
    _inherit = 'pos.session'

    def _create_bank_payment_moves(self, data):
        combine_receivables_bank = data.get('combine_receivables_bank')
        data['combine_receivables_bank'] = {}
        bank_payment_method_diffs = data.get('bank_payment_method_diffs')
        MoveLine = data.get('MoveLine')
        payment_method_to_receivable_lines = {}
        for payment_method, amounts in combine_receivables_bank.items():
            combine_receivable_line = MoveLine.create(
                self._get_combine_receivable_vals(payment_method, amounts['amount'], amounts['amount_converted']))
            payment_receivable_line = self._create_combine_account_payment(payment_method, amounts,
                                                                           diff_amount=bank_payment_method_diffs.get(
                                                                               payment_method.id) or 0)
            if payment_method.charges_applicable:
                payment_method_charges = 0
                for order in self.order_ids.filtered(
                        lambda rec: payment_method.id in rec.payment_ids.mapped('payment_method_id.id')):
                    amount_paid_by_changeable_payment_method = sum(
                        order.payment_ids.filtered(lambda rec: payment_method.id == rec.payment_method_id.id).mapped(
                            'amount'))
                    untaxed_paid_amount = (amount_paid_by_changeable_payment_method - (
                                amount_paid_by_changeable_payment_method * order.amount_tax / order.amount_total))
                    charges = (round((untaxed_paid_amount * payment_method.fees_rate / 100) + 1, 2))
                    payment_method_charges += payment_method.max_fees if payment_method.max_fees and payment_method.max_fees < charges else charges
                    print(order)
                combine_receivable_line['debit'] -= payment_method_charges
                tax_res = payment_method.tax_payable.compute_all(payment_method_charges).get('taxes')
                tax_amount = sum([rec.get('amount') for rec in tax_res])
                vals = {'debit': payment_method_charges - tax_amount, 'account_id': payment_method.account_id.id,
                        'name': _(self.name + ' - ' + payment_method.account_id.name)}
                combine_receivable_line.copy(vals)
                vals = {'debit': tax_amount, 'account_id': tax_res[0].get('account_id'),
                        'name': _(self.name + ' - ' + payment_method.account_id.name + ' Tax')}
                combine_receivable_line.copy(vals)

            payment_method_to_receivable_lines[payment_method] = combine_receivable_line | payment_receivable_line
            data = super(PosSession, self)._create_bank_payment_moves(data)
            data['combine_receivables_bank'] = combine_receivables_bank
        return data