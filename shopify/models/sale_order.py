import time
import pytz
import pprint
import logging
from .. import shopify
from datetime import timedelta
import urllib.parse as urlparse
from odoo import models, fields, tools, api, _
from .misc import convert_shopify_datetime_to_utc

_logger = logging.getLogger("Teqstars:Shopify")

FINANCIAL_STATUS = [('pending', 'Pending'),
                    ('authorized', 'Authorized'),
                    ('partially_paid', 'Partially Paid'),
                    ('paid', 'Paid'),
                    ('partially_refunded', 'Partially Refunded'),
                    ('refunded', 'Refunded'),
                    ('voided', 'Voided')]

FULFILLMENT_STATUS = [('fulfilled', 'Fulfilled'),
                      ('unfulfilled', 'Unfulfilled'),
                      ('partial', 'Partial'),
                      ('restocked', 'Restocked')]

SOURCE_NAME = [('web', 'Online Store'),
               ('pos', 'POS'),
               ('shopify_draft_order', 'Draft Orders'),
               ('iphone', 'iPhone'),
               ('android', 'Android')]


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.depends('fraud_analysis_ids')
    def _check_fraud_orders(self):
        for order_id in self:
            if any(fraud_analysis_id['recommendation'] == 'accept' for fraud_analysis_id in order_id.fraud_analysis_ids):
                order_id.is_fraud_order = True
            else:
                order_id.is_fraud_order = False

    shopify_closed_at = fields.Datetime("Order Closing Date", copy=False)
    fraud_analysis_ids = fields.One2many("shopify.fraud.analysis", "order_id", string="Fraud Analysis", copy=False)
    is_fraud_order = fields.Boolean('Fraud Order?', default=False, copy=False)
    # shopify_location_id = fields.Char('Shopify Location ID', copy=False)
    shopify_location_id = fields.Many2one("shopify.location.ts", "Shopify Location", copy=False)
    shopify_financial_status = fields.Selection(FINANCIAL_STATUS, "Financial Status", help="The status of payments associated with the order in marketplace.")
    fulfillment_status = fields.Selection(FULFILLMENT_STATUS, copy=False, help="The order's status in terms of fulfilled line items:\n\n"
                                                                               "Fulfilled: Every line item in the order has been fulfilled.\n"
                                                                               "Unfulfilled: None of the line items in the order have been fulfilled.\n"
                                                                               "Partial: At least one line item in the order has been fulfilled.\n"
                                                                               "Restocked: Every line item in the order has been restocked and the order canceled.")
    shopify_source_name = fields.Selection(SOURCE_NAME, string="Order Source", copy=False, help="Know source of Order creation.")
    shopify_order_source_name = fields.Char("Shopify Order Source", copy=False, help="Know source of Order creation.")

    def fetch_orders_from_shopify(self, from_date, to_date, shopify_fulfillment_status_ids, limit=250, mk_order_id=False):
        shopify_order_list, page_info = [], False
        if mk_order_id:
            order_list = []
            for order in ''.join(mk_order_id.split()).split(','):
                order_list.append(shopify.Order().find(order))
            return order_list
        utc_timezone = pytz.timezone("UTC")
        to_date = utc_timezone.localize(to_date)
        from_date = utc_timezone.localize(from_date)
        if 'Any' in shopify_fulfillment_status_ids.mapped('name'):
            shopify_fulfillment_status_ids = self.env.ref('shopify.shopify_order_status_any')
        from_import_screen = self.env.context.get('from_import_screen', False)
        for shopify_fulfillment_status_id in shopify_fulfillment_status_ids:
            while 1:
                if page_info:
                    page_wise_order_list = shopify.Order().find(limit=limit, page_info=page_info)
                else:
                    if from_import_screen:
                        page_wise_order_list = shopify.Order().find(status='any', fulfillment_status=shopify_fulfillment_status_id.status, created_at_min=from_date,
                                                                    created_at_max=to_date, limit=limit)
                    else:
                        page_wise_order_list = shopify.Order().find(status='any', fulfillment_status=shopify_fulfillment_status_id.status, updated_at_min=from_date,
                                                                    updated_at_max=to_date, limit=limit)
                page_url = page_wise_order_list.next_page_url
                parsed = urlparse.parse_qs(page_url)
                page_info = parsed.get('page_info', False) and parsed.get('page_info', False)[0] or False
                shopify_order_list = page_wise_order_list + shopify_order_list
                if not page_info:
                    break
        return shopify_order_list

    def check_validation_for_import_sale_orders(self, shopify_order_line_list, mk_instance_id, shopify_order_dict):
        odoo_product_variant_obj, is_importable, order_number = self.env['product.product'], True, shopify_order_dict.get('name', '')
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        mk_listing_item_obj, mk_listing_obj = self.env['mk.listing.item'], self.env['mk.listing']

        # validation for Financial workflow
        financial_workflow_config_id = self.validate_shopify_financial_workflow(shopify_order_dict, mk_instance_id)
        if not financial_workflow_config_id:
            return False, financial_workflow_config_id

        for shopify_order_line_dict in shopify_order_line_list:
            variant_id = shopify_order_line_dict.get('variant_id', False)
            if variant_id:
                shopify_variant = mk_listing_item_obj.search([('mk_id', '=', variant_id), ('mk_instance_id', '=', mk_instance_id.id)])
                if shopify_variant:
                    continue
                try:
                    shopify_variant = shopify.Variant().find(variant_id)
                except:
                    log_message = "IMPORT ORDER: Cannot find Shopify Listing Item ID {} in Shopify, Order reference {}.".format(variant_id, order_number)
                    self.env['mk.log'].create_update_log(
                        mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                    continue
                product_tmpl_id = shopify_order_line_dict.get('product_id', False)
                if shopify_variant:
                    shopify_variant_dict = shopify_variant.to_dict()
                    sku = shopify_variant_dict.get('sku', '')
                    if product_tmpl_id:
                        shopify_product = shopify.Product().find(product_tmpl_id)
                        shopify_product_dict = shopify_product.to_dict()
                        self.env['mk.queue.job'].do_import_listing_process(shopify_product_dict, mk_instance_id)
                    odoo_product_variant_id = odoo_product_variant_obj.search([('default_code', '=', sku)], limit=1)
                    if not odoo_product_variant_id:
                        log_message = "IMPORT ORDER: Marketplace Item SKU {} Not found for Order {}".format(sku, order_number)
                        self.env['mk.log'].create_update_log(
                            mk_log_id=mk_log_id, mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                        is_importable = False
                        break
        return is_importable, financial_workflow_config_id

    def validate_shopify_financial_workflow(self, shopify_order_dict, mk_instance_id):
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        main_gateway = shopify_order_dict.get('gateway', '') or 'Untitled'
        gateway_list = [transaction.get('gateway', 'Untitled') for transaction in shopify_order_dict.get('transactions', [{'gateway': 'Untitled'}])]
        main_workflow_config_id, not_found = False, False

        for gateway in gateway_list:
            shopify_payment_gateway_id = self.env['shopify.payment.gateway.ts'].search([('code', '=', gateway), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
            if not shopify_payment_gateway_id:
                shopify_payment_gateway_id = self.env['shopify.payment.gateway.ts'].create({'name': gateway, 'code': gateway, 'mk_instance_id': mk_instance_id.id})

            financial_workflow_config_id = self.env['shopify.financial.workflow.config'].search(
                ['|', ('financial_status', '=', shopify_order_dict.get('financial_status')), ('financial_status', '=', 'any'), ('mk_instance_id', '=', mk_instance_id.id),
                 ('payment_gateway_id', '=', shopify_payment_gateway_id.id)], limit=1)
            marketplace_workflow_id = financial_workflow_config_id.order_workflow_id or False
            if gateway == main_gateway:
                main_workflow_config_id = financial_workflow_config_id
            if not marketplace_workflow_id:
                log_message = "IMPORT ORDER: Financial Workflow Configuration not found for Shopify Order {}. Please configure the order workflow under the Workflow tab with Payment " \
                              "Gateway {} and Financial Status {} in Instance Configuration (Marketplaces > Configuration > Instance).".format(
                    shopify_order_dict.get('name'), shopify_payment_gateway_id.name, shopify_order_dict.get('financial_status'))
                self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id,
                                                     mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                not_found = True
        return False if not_found else main_workflow_config_id

    def get_shopify_order_source(self, shopify_order_dict):
        source_name = shopify_order_dict.get('source_name', 'web') or 'web'
        if source_name == 'web':
            readable_source_name = 'Online Store'
        elif source_name == 'pos':
            readable_source_name = 'POS'
        elif source_name == 'shopify_draft_order':
            readable_source_name = 'Draft Orders'
        elif source_name == 'iphone':
            readable_source_name = 'iPhone'
        elif source_name == 'android':
            readable_source_name = 'Android'
        else:
            readable_source_name = source_name
        return readable_source_name

    def create_shopify_sale_order(self, shopify_order_dict, mk_instance_id, customer_id, billing_customer_id, shipping_customer_id, pricelist_id, fiscal_position_id,
                                  payment_term_id, shopify_location_id, financial_workflow_config_id):
        # shopify_payment_gateway_id = self.validate_shopify_payment_gateway(shopify_order_dict, mk_instance_id)
        #
        # financial_workflow_config_id = self.validate_shopify_financial_workflow(shopify_order_dict, mk_instance_id, shopify_payment_gateway_id, customer_id)
        # if not shopify_payment_gateway_id or not financial_workflow_config_id:
        #     return False

        if financial_workflow_config_id:
            payment_term_id = financial_workflow_config_id.payment_term_id
            if payment_term_id:
                customer_id.write({'property_payment_term_id': payment_term_id.id})

        shopify_source_name = self.get_shopify_order_source(shopify_order_dict)

        sale_order_vals = {
            'state': 'draft',
            'partner_id': customer_id.id,
            'partner_invoice_id': billing_customer_id.ids[0] if billing_customer_id else customer_id.id,
            'partner_shipping_id': shipping_customer_id.ids[0] if shipping_customer_id else customer_id.id,
            'date_order': convert_shopify_datetime_to_utc(shopify_order_dict.get("created_at", "")),
            'expected_date': convert_shopify_datetime_to_utc(shopify_order_dict.get("created_at", "")),
            'company_id': mk_instance_id.company_id.id,
            'warehouse_id': shopify_location_id.order_warehouse_id and shopify_location_id.order_warehouse_id.id or mk_instance_id.warehouse_id.id,
            'fiscal_position_id': fiscal_position_id.id or False,
            'pricelist_id': pricelist_id or mk_instance_id.pricelist_id.id or False,
            'team_id': mk_instance_id.team_id.id or False,
        }
        sale_order_vals = self.prepare_sales_order_vals_ts(sale_order_vals)

        sale_order_vals.update({'note': shopify_order_dict.get('note'),
                                'mk_id': shopify_order_dict.get('id'),
                                'mk_order_number': shopify_order_dict.get('name'),
                                'shopify_financial_status': shopify_order_dict.get('financial_status'),
                                'mk_instance_id': mk_instance_id.id,
                                'shopify_location_id': shopify_location_id.id,
                                'fulfillment_status': shopify_order_dict.get('fulfillment_status', 'unfulfilled') or 'unfulfilled',
                                'shopify_order_source_name': shopify_source_name or ''})

        if mk_instance_id.use_marketplace_sequence:
            sale_order_vals.update({'name': shopify_order_dict.get("name", '')})

        if financial_workflow_config_id:
            marketplace_workflow_id = financial_workflow_config_id.order_workflow_id
            sale_order_vals.update({
                'picking_policy': marketplace_workflow_id.picking_policy,
                'payment_term_id': payment_term_id.id,
                'order_workflow_id': marketplace_workflow_id.id})

        order_id = self.create(sale_order_vals)
        return order_id

    def create_sale_order_line_without_product(self, shopify_order_currency, shopify_order_line_dict, tax_ids, odoo_product_id):
        sale_order_line_obj = self.env['sale.order.line']
        description = shopify_order_line_dict.get('name', shopify_order_line_dict.get('title', 'Untitled'))

        price = shopify_order_line_dict.get('price')
        order_currency_id = self.env['res.currency'].search([('name', '=', shopify_order_currency)], limit=1)
        if order_currency_id:
            price = order_currency_id._convert(float(price), self.currency_id, self.company_id, fields.Date.today())
        line_vals = {
            'name': description,
            'product_id': odoo_product_id.id or False,
            'order_id': self.id,
            'company_id': self.company_id.id,
            'product_uom': odoo_product_id.uom_id and odoo_product_id.uom_id.id or False,
            'price_unit': price,
            'order_qty': shopify_order_line_dict.get('quantity', 1) or 1,
        }

        order_line_data = sale_order_line_obj.prepare_sale_order_line_ts(line_vals)

        order_line_data.update({
            'name': description,
            'tax_id': tax_ids,
            'mk_id': shopify_order_line_dict.get('id')
        })
        order_line = sale_order_line_obj.create(order_line_data)
        return order_line

    def create_sale_order_line_ts(self, shopify_order_currency, shopify_order_line_dict, tax_ids, odoo_product_id, order_id, is_delivery=False, description='', is_discount=False):
        sale_order_line_obj = self.env['sale.order.line']

        price = shopify_order_line_dict.get('price')
        order_currency_id = self.env['res.currency'].search([('name', '=', shopify_order_currency)], limit=1)
        if order_currency_id:
            price = order_currency_id._convert(float(price), order_id.currency_id, order_id.company_id, fields.Date.today())
        line_vals = {
            'name': description if description else (shopify_order_line_dict.get('name') or odoo_product_id.name),
            'product_id': odoo_product_id.id or False,
            'order_id': order_id.id,
            'company_id': order_id.company_id.id,
            'product_uom': odoo_product_id.uom_id and odoo_product_id.uom_id.id or False,
            'price_unit': price,
            'order_qty': shopify_order_line_dict.get('quantity', 1) or 1,
        }

        order_line_data = sale_order_line_obj.prepare_sale_order_line_ts(line_vals)

        order_line_data.update({
            'name': description if description else (shopify_order_line_dict.get('name') or odoo_product_id.name),
            'tax_id': tax_ids,
            'is_delivery': is_delivery,
            'is_discount': is_discount,
            'mk_id': shopify_order_line_dict.get('id')
        })
        order_line = sale_order_line_obj.create(order_line_data)
        return order_line

    def get_shopify_delivery_method(self, carrier_name, mk_instance_id):
        carrier_obj = self.env['delivery.carrier']
        carrier_id = carrier_obj.search(['|', ('name', '=', carrier_name), ('shopify_code', '=', carrier_name)], limit=1)
        if not carrier_id:
            carrier_id = carrier_obj.search(['|', ('name', 'ilike', carrier_name), ('shopify_code', 'ilike', carrier_name)], limit=1)
        if not carrier_id:
            carrier_id = carrier_obj.create({'name': carrier_name, 'shopify_code': carrier_name, 'product_id': mk_instance_id.delivery_product_id.id})
        return carrier_id

    def create_shopify_shipping_line(self, mk_instance_id, shopify_order_dict, order_id):

        for shopify_shipping_dict in shopify_order_dict.get('shipping_lines', []):
            tax_line_list = []
            for tax_dict in shopify_shipping_dict.get('tax_lines', []):
                if float(tax_dict.get('price', 0.0)) > 0.0:
                    tax_line_list.append({'rate': tax_dict.get('rate', ''), 'title': tax_dict.get('title', '')})

            tax_ids = self.get_odoo_tax(mk_instance_id, tax_line_list, shopify_order_dict.get('taxes_included'))
            carrier_name = shopify_shipping_dict.get('title', 'Shopify Delivery Method')
            carrier_id = self.get_shopify_delivery_method(carrier_name, mk_instance_id)
            order_id.write({'carrier_id': carrier_id.id})
            shipping_product = carrier_id.product_id
            self.create_sale_order_line_ts(shopify_order_dict.get('currency'), shopify_shipping_dict, tax_ids, shipping_product, order_id, is_delivery=True,
                                           description=carrier_name)
            discount_amount = sum(
                [float(discount_allocation.get('amount', 0.0)) for discount_allocation in shopify_shipping_dict.get('discount_allocations') if discount_allocation])
            if discount_amount > 0.0:
                discount_desc = "Discount: {}".format(mk_instance_id.discount_product_id.name)
                self.create_sale_order_line_ts(shopify_order_dict.get('currency'), {'price': float(discount_amount) * -1, 'id': shopify_order_dict.get('id')},
                                               tax_ids, mk_instance_id.discount_product_id, order_id, is_discount=True, description=discount_desc)

    def create_sale_order_line_shopify(self, mk_instance_id, shopify_order_dict, order_id):
        shopify_order_line_list = shopify_order_dict.get('line_items')
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        for shopify_order_line_dict in shopify_order_line_list:
            shopify_product_variant_id = self.get_mk_listing_item_for_mk_order(shopify_order_line_dict.get('variant_id'), mk_instance_id)
            if not shopify_product_variant_id and shopify_order_line_dict.get('variant_id'):
                log_message = "IMPORT ORDER: Shopify Variant not found for Shopify Order ID {}, Variant ID: {} and Name: {}.".format(order_id.mk_id,
                                                                                                                                     shopify_order_line_dict.get('variant_id'),
                                                                                                                                     shopify_order_line_dict.get('title', ''))
                self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                     mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                return False
            odoo_product_id = shopify_product_variant_id.product_id
            taxes_included = shopify_order_dict.get('taxes_included', False)
            taxable = shopify_order_line_dict.get('taxable')
            tax_ids = False
            if taxable:
                tax_line_list = []
                for tax_dict in shopify_order_line_dict.get('tax_lines', []):
                    if float(tax_dict.get('price', 0.0)) > 0.0:
                        tax_line_list.append({'rate': tax_dict.get('rate'), 'title': tax_dict.get('title')})
                tax_ids = self.get_odoo_tax(mk_instance_id, tax_line_list, taxes_included)
            if shopify_product_variant_id:
                order_line = self.create_sale_order_line_ts(shopify_order_dict.get('currency'), shopify_order_line_dict, tax_ids, odoo_product_id, order_id)
            else:
                if not shopify_order_line_dict.get('variant_id', False):
                    if not mk_instance_id.custom_product_id:
                        log_message = "IMPORT ORDER: Shopify Custom Product not found for Shopify Order ID {}, Please set Product in Custom Product field in Order tab of Instance " \
                                      "configuration.".format(order_id.mk_id)
                        self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                             mk_log_line_dict={
                                                                 'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
                        return False
                    order_line = order_id.create_sale_order_line_without_product(shopify_order_dict.get('currency'), shopify_order_line_dict, tax_ids,
                                                                                 mk_instance_id.custom_product_id)

            discount_amount = sum(
                [float(discount_allocation.get('amount', 0.0)) for discount_allocation in shopify_order_line_dict.get('discount_allocations') if discount_allocation])
            if discount_amount > 0.0:
                discount_desc = "Discount: {}".format(order_line.product_id.name)
                self.create_sale_order_line_ts(shopify_order_dict.get('currency'), {'price': float(discount_amount) * -1, 'id': shopify_order_dict.get('id')},
                                               tax_ids, mk_instance_id.discount_product_id, order_id, is_discount=True, description=discount_desc)
                if order_line:
                    order_line.shopify_discount_amount = discount_amount
        self.create_shopify_shipping_line(mk_instance_id, shopify_order_dict, order_id)
        return True

    def process_import_order_from_shopify_ts(self, shopify_order_dict, mk_instance_id):
        shopify_location_obj, partner_obj = self.env['shopify.location.ts'], self.env['res.partner']
        mk_log_id = self.env.context.get('mk_log_id', False)
        queue_line_id = self.env.context.get('queue_line_id', False)
        shopify_order_name = shopify_order_dict.get('name', '')
        existing_order_id = self.search([('mk_id', '=', shopify_order_dict.get('id')), ('mk_instance_id', '=', mk_instance_id.id)])
        if existing_order_id:
            fulfillment_status = shopify_order_dict.get('fulfillment_status', 'unfulfilled') or 'unfulfilled'
            existing_order_id.write({'shopify_financial_status': shopify_order_dict.get('financial_status'),
                                     'fulfillment_status': fulfillment_status,
                                     'updated_in_marketplace': True if fulfillment_status == 'fulfilled' else False})
            log_message = "IMPORT ORDER: Shopify Order {}({}) is already imported.".format(shopify_order_name, shopify_order_dict.get('id'))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                 mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return existing_order_id

        shopify_order_line_list = shopify_order_dict.get('line_items')
        is_importable, financial_workflow_config_id = self.check_validation_for_import_sale_orders(shopify_order_line_list, mk_instance_id, shopify_order_dict)
        if not is_importable:
            return False

        company_customer_id = False
        if mk_instance_id.is_create_company_contact:
            default_address_dict = shopify_order_dict.get('customer', {}).get('default_address') if shopify_order_dict.get('customer', {}).get('default_address',
                                                                                                                                               False) else shopify_order_dict.get(
                'customer', {})
            is_company = True if default_address_dict.get('company', False) else False
            if is_company:
                company_customer_id = partner_obj.create_update_shopify_customers(default_address_dict, mk_instance_id)
        if not shopify_order_dict.get('customer', False) and shopify_order_dict.get('source_name', '') == 'pos' and mk_instance_id.default_pos_customer_id:
            customer_id = mk_instance_id.default_pos_customer_id
        else:
            customer_id = partner_obj.create_update_shopify_customers(shopify_order_dict.get('customer', {}), mk_instance_id, parent_id=company_customer_id)
        if not customer_id:
            log_message = "IMPORT ORDER: Customer not found in Shopify Order No: {}({})".format(shopify_order_name, shopify_order_dict.get('id'))
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                 mk_log_line_dict={'error': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
            return False
        if not shopify_order_dict.get('billing_address', shopify_order_dict.get('customer', False)):
            billing_customer_id = customer_id
        else:
            billing_customer_id = partner_obj.create_update_shopify_customers(shopify_order_dict.get('billing_address', shopify_order_dict.get('customer', {})), mk_instance_id,
                                                                              type='invoice', parent_id=company_customer_id or customer_id)
        if not shopify_order_dict.get('shipping_address', shopify_order_dict.get('customer', False)):
            shipping_customer_id = customer_id
        else:
            shipping_customer_id = partner_obj.create_update_shopify_customers(shopify_order_dict.get('shipping_address', shopify_order_dict.get('customer', {})), mk_instance_id,
                                                                               type='delivery', parent_id=company_customer_id or customer_id)

        shopify_location_id = shopify_order_dict.get('location_id', False) or shopify_order_dict.get('fulfillments', False) and shopify_order_dict.get('fulfillments', False)[
            0].get('location_id') or False
        if not shopify_location_id:
            shopify_location_id = shopify_location_obj.search([('is_default_location', '=', True), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)
        else:
            shopify_location_id = shopify_location_obj.search([('shopify_location_id', '=', shopify_location_id), ('mk_instance_id', '=', mk_instance_id.id)], limit=1)

        customer = self.new({'partner_id': customer_id.id})
        customer.onchange_partner_id()
        customer_dict = customer._convert_to_write({name: customer[name] for name in customer._cache})
        pricelist_id = customer_dict.get('pricelist_id', False)
        fiscal_position_id = customer_id.property_account_position_id
        payment_term_id = customer_dict.get('payment_term_id', False)

        order_id = self.create_shopify_sale_order(shopify_order_dict, mk_instance_id, customer_id, billing_customer_id, shipping_customer_id, pricelist_id, fiscal_position_id,
                                                  payment_term_id, shopify_location_id, financial_workflow_config_id)
        if not order_id:
            return False

        if not order_id.create_sale_order_line_shopify(mk_instance_id, shopify_order_dict, order_id):
            order_id.unlink()
            return False
        if mk_instance_id.is_fetch_fraud_analysis_data:
            self.env['shopify.fraud.analysis'].create_fraud_analysis(shopify_order_dict.get('id'), order_id)
        order_id.with_context(create_date=convert_shopify_datetime_to_utc(shopify_order_dict.get("created_at", "")),
                              order_dict=shopify_order_dict).do_marketplace_workflow_process()
        if order_id:
            log_message = 'IMPORT ORDER: Successfully imported marketplace order {}({})'.format(order_id.name, order_id.mk_id)
            self.env['mk.log'].create_update_log(mk_log_id=mk_log_id,
                                                 mk_log_line_dict={'success': [{'log_message': log_message, 'queue_job_line_id': queue_line_id and queue_line_id.id or False}]})
        return order_id

    def fetch_order_transaction_from_shopify(self, shopify_order_list):
        if not isinstance(shopify_order_list, list):
            shopify_order_list = [shopify_order_list]
        for order in shopify_order_list:
            transactions = shopify.Transaction.find(order_id=order.get_id())
            trans_list = []
            [trans_list.append(transaction.to_dict()) for transaction in transactions]
            if trans_list:
                order.attributes['transactions'] = trans_list
        return shopify_order_list

    def shopify_import_orders(self, mk_instance_ids, from_date=False, to_date=False, mk_order_id=False):
        if not isinstance(mk_instance_ids, list):
            mk_instance_ids = [mk_instance_ids]
        for mk_instance_id in mk_instance_ids:
            mk_instance_id.connection_to_shopify()
            mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='import')
            if not from_date:
                from_date = mk_instance_id.last_order_sync_date if mk_instance_id.last_order_sync_date else fields.Datetime.now() - timedelta(3)
            if not to_date:
                to_date = fields.Datetime.now()
            shopify_fulfillment_status_ids = mk_instance_id.fulfillment_status_ids
            shopify_order_list = self.fetch_orders_from_shopify(from_date, to_date, shopify_fulfillment_status_ids, limit=mk_instance_id.api_limit, mk_order_id=mk_order_id)
            self.fetch_order_transaction_from_shopify(shopify_order_list)  # Fetch payment transactions from Shopify and set in order dict.
            if mk_order_id and shopify_order_list:
                for shopify_order in shopify_order_list:
                    order_id = self.with_context(mk_log_id=mk_log_id).process_import_order_from_shopify_ts(shopify_order.to_dict(), mk_instance_id)
                if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                    mk_log_id.unlink()
                self._cr.commit()
                return True
            if shopify_order_list:
                batch_size = mk_instance_id.queue_batch_limit or 100
                for shopify_orders in tools.split_every(batch_size, shopify_order_list):
                    queue_id = mk_instance_id.action_create_queue(type='order')
                    for order in shopify_orders:
                        shopify_order_dict = order.to_dict()
                        name = shopify_order_dict.get('name', '') or ''
                        line_vals = {
                            'mk_id': shopify_order_dict.get('id') or '',
                            'state': 'draft',
                            'name': name.strip(),
                            'data_to_process': pprint.pformat(shopify_order_dict),
                            'mk_instance_id': mk_instance_id.id,
                        }
                        queue_id.action_create_queue_lines(line_vals)
            if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                mk_log_id.unlink()
            mk_instance_id.last_order_sync_date = to_date
        return True

    def get_shopify_sale_orders(self, mk_instance_id):
        warehouse_ids = self.env['shopify.location.ts'].search([('mk_instance_id', '=', mk_instance_id.id)]).mapped('warehouse_id')
        if not warehouse_ids:
            warehouse_ids = mk_instance_id.warehouse_id
        shopify_order_ids = self.search([('mk_id', '!=', False),
                                         ('updated_in_marketplace', '=', False),
                                         ('warehouse_id', 'in', warehouse_ids.ids),
                                         ('mk_instance_id', '=', mk_instance_id.id)], order='date_order')
        return shopify_order_ids

    def close_shopify_order(self, shopify_order, odoo_order_id, close_order_after_fulfillment=True):
        if not close_order_after_fulfillment:
            return False
        shopify_order.close()
        odoo_order_id.write({'shopify_closed_at': fields.Datetime.now()})
        return True

    def shopify_prepare_fulfillment_line_vals(self, picking_id):
        line_item_list = []
        mrp = self.env['ir.module.module'].search([('name', '=', 'mrp'), ('state', '=', 'installed')])
        for move in picking_id.move_lines:
            if int(move.quantity_done) > 0 and move.sale_line_id.mk_id:
                if mrp and self.env['mrp.bom']._bom_find(product=move.sale_line_id.product_id, bom_type='phantom'):
                    quantity = move.sale_line_id.product_uom_qty
                else:
                    quantity = move.quantity_done
                line_item_list.append({'id': move.sale_line_id.mk_id, 'quantity': int(quantity)})
        return line_item_list

    def shopify_update_picking_retry_count(self, picking_id):
        if picking_id.no_of_retry_count == 2:
            picking_id.message_post(
                body="System tried 3 times to update this order in Shopify but something went wrong! Therefor, Manual attention needed to process this order in Shopify.")
            picking_id.no_of_retry_count += 1
        return picking_id.no_of_retry_count

    def shopify_update_order_status(self, mk_instance_ids):
        if not isinstance(mk_instance_ids, list):
            mk_instance_ids = [mk_instance_ids]
        for mk_instance_id in mk_instance_ids:
            mk_log_id = self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, operation_type='export')
            mk_log_line_dict = self.env.context.get('mk_log_line_dict', {'error': [], 'success': []})
            mk_instance_id.connection_to_shopify()
            shopify_order_ids = self.get_shopify_sale_orders(mk_instance_id)
            for shopify_order_id in shopify_order_ids:
                shopify_order, fulfillment_result = False, False
                picking_ids = shopify_order_id.picking_ids.filtered(
                    lambda x: not x.updated_in_marketplace and x.state == 'done' and x.location_dest_id.usage == 'customer' and x.no_of_retry_count < 3)
                if not picking_ids:
                    continue
                try:
                    shopify_order = shopify.Order.find(shopify_order_id.mk_id)
                except Exception as e:
                    if e.code == 429:
                        time.sleep(3)
                        shopify_order = shopify.Order.find(shopify_order_id.mk_id)
                if shopify_order.to_dict().get('fulfillment_status') == 'fulfilled':
                    picking_ids.write({'updated_in_marketplace': True})
                    shopify_order_id.write({'fulfillment_status': 'fulfilled', 'updated_in_marketplace': True})
                    mk_log_line_dict['success'].append({'log_message': 'UPDATE ORDER STATUS: Shopify order {} is already updated in Shopify.'.format(shopify_order_id.name)})
                    shopify_order_id.message_post(body="Already fulfilled in Shopify.")
                    for picking_id in picking_ids:
                        picking_id.message_post(body="Already fulfilled in Shopify.")
                    continue
                for picking_id in picking_ids:
                    if not picking_id.sale_id.order_line.mapped('mk_id'):
                        log_message = 'Cannot update order status because Shopify Order Line ID not found in Order {}'.format(shopify_order_id.name)
                        mk_log_line_dict['error'].append({'log_message': 'UPDATE ORDER STATUS: {}'.format(log_message)})
                        self.shopify_update_picking_retry_count(picking_id)
                        continue
                    carrier_name = picking_id.carrier_id and picking_id.carrier_id.shopify_code or picking_id.carrier_id.name or ''
                    line_item_list = self.shopify_prepare_fulfillment_line_vals(picking_id)
                    tracking_no_list = [picking_id.carrier_tracking_ref] if picking_id.carrier_tracking_ref else []
                    # thanks to https://stackoverflow.com/a/9427216
                    # below line is used to remove duplicate dict because in Kit type product it may possible that duplicate line dict will be created.
                    line_item_list = [dict(t) for t in {tuple(d.items()) for d in line_item_list}]
                    if not line_item_list:
                        log_message = 'Order lines not found for Shopify Order {} while trying to update Order status'.format(shopify_order_id.name)
                        mk_log_line_dict['error'].append({'log_message': 'UPDATE ORDER STATUS: {}'.format(log_message)})
                        self.shopify_update_picking_retry_count(picking_id)
                        continue

                    shopify_location_id = shopify_order_id.shopify_location_id or False
                    if not shopify_location_id:
                        shopify_location_id = self.env['shopify.location.ts'].search([('is_default_location', '=', True), ('mk_instance_id', '=', mk_instance_id.id)])
                    if not shopify_location_id:
                        log_message = 'Location not found in Shopify Order {} while trying to update Order status'.format(shopify_order_id.name)
                        mk_log_line_dict['error'].append({'log_message': 'UPDATE ORDER STATUS: {}'.format(log_message)})
                        self.shopify_update_picking_retry_count(picking_id)
                        continue
                    try:
                        new_fulfillment = shopify.Fulfillment({'order_id': shopify_order_id.mk_id,
                                                               'location_id': shopify_location_id.shopify_location_id,
                                                               'tracking_numbers': tracking_no_list,
                                                               'tracking_company': carrier_name,
                                                               'line_items': line_item_list,
                                                               'notify_customer': mk_instance_id.is_notify_customer})
                        fulfillment_result = new_fulfillment.save()
                    except Exception as e:
                        if e.code == 429:
                            time.sleep(3)
                            new_fulfillment = shopify.Fulfillment({'order_id': shopify_order_id.mk_id,
                                                                   'location_id': shopify_location_id.shopify_location_id,
                                                                   'tracking_numbers': tracking_no_list,
                                                                   'tracking_company': carrier_name,
                                                                   'line_items': line_item_list,
                                                                   'notify_customer': mk_instance_id.is_notify_customer})
                            fulfillment_result = new_fulfillment.save()
                        else:
                            log_message = 'Error while trying to update Order status of Shopify Order {}.ERROR: {}'.format(shopify_order_id.name, e)
                            mk_log_line_dict['error'].append({'log_message': 'UPDATE ORDER STATUS: {}'.format(log_message)})
                            self.shopify_update_picking_retry_count(picking_id)
                            continue
                    if not fulfillment_result:
                        errors = ''
                        if new_fulfillment.errors and new_fulfillment.errors.errors:
                            errors = ",".join(error for error in new_fulfillment.errors.errors.get('base'))
                        log_message = 'Shopify Order {} is not updated due to some issue. REASON: {}'.format(shopify_order_id.name, errors)
                        mk_log_line_dict['error'].append({'log_message': 'UPDATE ORDER STATUS: {}'.format(log_message)})
                        self.shopify_update_picking_retry_count(picking_id)
                        continue
                    picking_id.write({'updated_in_marketplace': True})
                    # if all([picking.updated_in_marketplace for picking in
                    #         shopify_order_id.picking_ids.filtered(lambda x: x.state not in ['draft', 'cancel'] and x.location_dest_id.usage == 'customer')]):
                    # shopify_order_id.write({'fulfillment_status': 'fulfilled', 'updated_in_marketplace': True})
                    mk_log_line_dict['success'].append({'log_message': 'UPDATE ORDER STATUS: Successfully updated Shopify order {}'.format(shopify_order_id.name)})
                    msg = "Delivery {} fulfilled {} items in Shopify from {} Location.".format(picking_id.name, len(line_item_list), shopify_location_id.name)
                    shopify_order_id.message_post(body=msg)
                    picking_id.message_post(body=msg)
                if all(shopify_order_id.order_line.mapped('move_ids').mapped('picking_id').mapped('updated_in_marketplace')):
                    shopify_order_id.write({'fulfillment_status': 'fulfilled', 'updated_in_marketplace': True})
                    if fulfillment_result:
                        self.close_shopify_order(shopify_order, shopify_order_id, mk_instance_id.close_order_after_fulfillment)
                self._cr.commit()
            self.env['mk.log'].create_update_log(mk_instance_id=mk_instance_id, mk_log_id=mk_log_id, mk_log_line_dict=mk_log_line_dict)
            if not mk_log_id.log_line_ids and not self.env.context.get('log_id', False):
                mk_log_id.unlink()
        return True

    def cancel_in_shopify(self):
        view = self.env.ref('shopify.cancel_in_shopify_form_view')
        context = dict(self._context)
        context.update({'active_model': 'sale.order', 'active_id': self.id, 'active_ids': self.ids})
        return {
            'name': _('Cancel Order In Shopify'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mk.cancel.order',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'context': context
        }

    def refund_in_shopify(self):
        view = self.env.ref('shopify.refund_in_shopify_form_view')
        context = dict(self._context)
        context.update({'active_model': 'sale.order', 'active_id': self.id, 'active_ids': self.ids})
        return {
            'name': _('Refund Order In Shopify'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'mk.cancel.order',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'context': context
        }

    def cron_auto_import_shopify_orders(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            self.shopify_import_orders(mk_instance_id)
        return True

    def cron_auto_update_order_status(self, mk_instance_id):
        mk_instance_id = self.env['mk.instance'].browse(mk_instance_id)
        if mk_instance_id.state == 'confirmed':
            self.shopify_update_order_status(mk_instance_id)
        return True

    def open_sale_order_in_marketplace(self):
        self.ensure_one()
        if hasattr(self, '%s_open_sale_order_in_marketplace' % self.mk_instance_id.marketplace):
            url = getattr(self, '%s_open_sale_order_in_marketplace' % self.mk_instance_id.marketplace)()
            if url:
                client_action = {
                    'type': 'ir.actions.act_url',
                    'name': "Marketplace URL",
                    'target': 'new',
                    'url': url,
                }
                return client_action

    def shopify_open_sale_order_in_marketplace(self):
        marketplace_url = self.mk_instance_id.shop_url + '/admin/orders/' + self.mk_id
        return marketplace_url

    # def _prepare_payment_vals(self, order_workflow_id, invoice_id):
    #     payment_vals = super(SaleOrder, self)._prepare_payment_vals(order_workflow_id, invoice_id)
    #     if self.env.context.get('transaction', False) and self.env.context.get('transaction', {}).get('amount', False):
    #          payment_vals.update({'amount':self.env.context.get('transaction', {}).get('amount', False) or invoice_id.amount_residual})
    #     return payment_vals

    def shopify_reconcile_invoice(self, order_workflow_id, invoice_id, transaction):
        amount = float(transaction.get('amount', 0.0)) if transaction and isinstance(transaction, dict) and transaction.get('amount', 0.0) else 0.0
        payment_vals = self._prepare_payment_vals(order_workflow_id, invoice_id, amount=amount)
        payment = self.env['account.payment'].create(payment_vals)
        liquidity_lines, counterpart_lines, writeoff_lines = payment._seek_for_lines()
        payment.action_post()
        (counterpart_lines + invoice_id.line_ids.filtered(lambda line: line.account_internal_type == 'receivable')).reconcile()

    def shopify_pay_and_reconcile(self, order_workflow_id, invoice_id):
        transactions = self.env.context.get('order_dict', {}).get('transactions', {})
        if transactions:
            for transaction in transactions:
                self.shopify_reconcile_invoice(order_workflow_id, invoice_id, transaction)
        else:
            self.shopify_reconcile_invoice(order_workflow_id, invoice_id, False)
        return True


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    shopify_discount_amount = fields.Float("Shopify Discount Amount")
