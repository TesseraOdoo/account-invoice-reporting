# Copyright 2017-2024 Tecnativa - Carlos Dauden
# Copyright 2018 Tecnativa - David Vidal
# Copyright 2018-2019 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from datetime import datetime

from odoo import api, models
from odoo.tools import float_round


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def _sort_grouped_lines(self, lines_dic):
        DTF = "%Y-%m-%d %H:%M:%S"
        min_date = datetime.min
        return sorted(
            lines_dic,
            key=lambda x: (
                x["picking"]
                and (
                    (x["picking"].date or min_date).strftime(DTF),
                    (x["picking"].date_done or x["picking"].date or min_date).strftime(
                        DTF
                    ),
                )
                or ("", ""),
                x.get("is_last_section_notes", False),
            ),
        )

    def _get_signed_quantity_done(self, invoice_line, move, sign):
        """Hook method. Usage example:
        account_invoice_report_grouped_by_picking_sale_mrp module
        """
        if move.location_id.usage == "customer":
            return -move.quantity * sign
        if move.location_dest_id.usage == "customer":
            return move.quantity * sign
        return 0

    def _get_refund_quantity_by_picking(self, invoice_line, qty_by_picking):
        """A credit note line can be linked to more stock quantity than the
        refunded one: e.g. a partial or price refund made with the reversal
        wizard keeps the moves of the original invoice line. Print in each
        picking only the refunded quantity, never the delivered one."""
        to_assign = invoice_line.quantity
        res = {}
        for picking, qty in qty_by_picking.items():
            qty = min(abs(qty), to_assign)
            if qty or not res:
                res[picking] = qty
            to_assign -= qty
        return res

    def _process_section_note_lines_grouped(
        self, previous_section, previous_note, lines_dic, pick_order=None
    ):
        """Processes section and note lines, grouping them by order."""
        for line in [previous_section, previous_note]:
            if line:
                key = (pick_order, line) if pick_order else line
                lines_dic.setdefault(key, 0.0)

    def _get_grouped_by_picking_sorted_lines(self):
        """Sorts the invoice lines to be grouped by picking."""
        return self.invoice_line_ids.sorted(
            lambda ln: (-ln.sequence, ln.date, ln.move_name, -ln.id), reverse=True
        )

    def lines_grouped_by_picking(self):
        """This prepares a data structure for printing the invoice report
        grouped by pickings."""
        self.ensure_one()
        if not self.journal_id.show_group_by_picking_report:
            return [
                {
                    "picking": self.env["stock.picking"],
                    "line": line,
                    "quantity": line.quantity,
                }
                for line in self.invoice_line_ids
            ]
        picking_dict = {}
        lines_dict = {}
        picking_obj = self.env["stock.picking"]
        # Determine the sign based on the move type
        # Not change sign if the credit note has been created from reverse move option
        # and it has the same pickings related than the reversed invoice instead of sale
        # order invoicing process after picking reverse transfer
        sign = (
            -1.0
            if self.move_type == "out_refund"
            and (
                not self.reversed_entry_id
                or self.reversed_entry_id.picking_ids != self.picking_ids
            )
            else 1.0
        )
        # Let's get first a correspondance between pickings and sales order
        so_dict = {p.sale_id: p for p in self.picking_ids if p.sale_id}
        # Now group by picking by direct link or via same SO as picking's one
        previous_section = previous_note = False
        last_section_notes = []
        sorted_lines = self._get_grouped_by_picking_sorted_lines()
        for line in sorted_lines:
            # Process section or note lines
            if line.display_type in ["line_section", "line_note"]:
                if line.display_type == "line_section":
                    previous_section = line
                else:
                    previous_note = line
                last_section_notes.append(
                    {
                        "picking": picking_obj,
                        "line": line,
                        "qty": 0.0,
                        "is_last_section_notes": True,
                    }
                )
                continue
            # Reset sections and notes when encountering a regular line
            last_section_notes = []
            remaining_qty = line.quantity
            # Process moves related to the line
            qty_by_picking = {}
            for move in line.move_line_ids:
                qty = self._get_signed_quantity_done(line, move, sign)
                qty_by_picking[move.picking_id] = (
                    qty_by_picking.get(move.picking_id, 0.0) + qty
                )
            if self.move_type == "out_refund" and qty_by_picking:
                qty_by_picking = self._get_refund_quantity_by_picking(
                    line, qty_by_picking
                )
            for picking, qty in qty_by_picking.items():
                key = (picking, line)
                self._process_section_note_lines_grouped(
                    previous_section, previous_note, picking_dict, picking
                )
                picking_dict[key] = picking_dict.get(key, 0.0) + qty
                remaining_qty -= qty
            # Process sale order lines without moves
            if not line.move_line_ids and line.sale_line_ids:
                for so_line in line.sale_line_ids:
                    picking = so_dict.get(so_line.order_id)
                    if picking:
                        key = (picking, line)
                        self._process_section_note_lines_grouped(
                            previous_section, previous_note, picking_dict, picking
                        )
                        qty = min(so_line.product_uom_qty, remaining_qty)
                        picking_dict[key] = picking_dict.get(key, 0.0) + qty
                        remaining_qty -= qty
            # Process lines without moves or sale orders
            elif not line.move_line_ids and not line.sale_line_ids:
                key = (picking_obj, line)
                self._process_section_note_lines_grouped(
                    previous_section, previous_note, lines_dict
                )
                qty = line.quantity
                picking_dict[key] = picking_dict.get(key, 0.0) + qty
                remaining_qty -= qty
            remaining_qty = float_round(
                remaining_qty,
                precision_rounding=line.product_id.uom_id.rounding or 0.01,
            )
            if remaining_qty:
                self._process_section_note_lines_grouped(
                    previous_section, previous_note, lines_dict
                )
                lines_dict[line] = remaining_qty
        no_picking = [
            {"picking": picking_obj, "line": key, "quantity": value}
            for key, value in lines_dict.items()
        ]
        with_picking = [
            {"picking": key[0], "line": key[1], "quantity": value}
            for key, value in picking_dict.items()
        ]
        return no_picking + self._sort_grouped_lines(with_picking + last_section_notes)
