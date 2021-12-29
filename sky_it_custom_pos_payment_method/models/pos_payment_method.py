# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class FollowupLine(models.Model):
    _inherit = 'pos.payment.method'

    charges_applicable = fields.Boolean(string='Charges Applicable')
    account_id = fields.Many2one(comodel_name='account.account', string="Expense Account")
    fees_rate = fields.Float(string='Fees Rate')
    additional_fees = fields.Float(string='Additional Fees')
    tax_payable = fields.Many2one(comodel_name='account.tax', string='Tax')
    max_fees = fields.Float(string="Max Fees")

