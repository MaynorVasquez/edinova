# Copyright (c) 2025, Maynor Vasquez and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from edinova.api.erpnext_orden_venta import crear_ordenes_venta


class EdinovaSincronizarOrdenesdeVenta(Document):
    def after_insert(self):
        if self.fecha_inicio:
            try:
                # Ejecutar la función que crea las órdenes
                resultado = crear_ordenes_venta(str(self.fecha_inicio))

                # Guardar log en el mismo registro
                self.status = "Exitoso"
                self.total_registros = resultado.get("total", 0)
                self.detalle = frappe.as_json(resultado.get("data", []))

            except Exception:
                self.status = "Error"
                self.error = frappe.get_traceback()

            # Guardar cambios en el registro
            self.save(ignore_permissions=True)
