import json
import frappe
from datetime import datetime
from .edinova_orden_venta import get_ordenes_venta

def crear_ordenes_venta(fecha):
    import json
    ordenes = get_ordenes_venta(fecha)
    print(f"Órdenes recibidas: {len(ordenes)}")

    ordenes_json = []

    for ov in ordenes:
        numero_po = ov["purchaseOrderNumber"]["po"]
        print(f"Procesando orden: {numero_po}")

        # Convertir fecha
        date_str = str(ov["purchaseOrderNumber"]["datePO"])
        dt = datetime.strptime(date_str, "%Y%m%d%H%M")
        po_date = dt.strftime("%Y-%m-%d")

        if frappe.db.exists("Sales Order", {"po_no": numero_po}):
            print(f"Orden {numero_po} ya existe, saltando")
            continue

        #nombre_cliente = ov["buyer"]["name"]
        #cliente = frappe.db.get_value("Customer", {"customer_name": nombre_cliente}, "customer_name")
        cliente =  frappe.db.get_single_value("Descuentos Negociados", "cardcode")
        if not cliente:
           # frappe.log_error(f"Cliente con NIT {nombre_cliente} no encontrado", "Import OV")
            continue

        price_list = frappe.db.get_value("Customer", cliente, "default_price_list")

        company = frappe.db.get_single_value("Descuentos Negociados", "company")

        # Crear documento Sales Order
        sales_order = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": cliente,
            "company": company,  # <--- tu compañía
            "transaction_date": frappe.utils.nowdate(),
            "delivery_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
            "po_date": po_date,
            "selling_price_list": price_list,
            "po_no": numero_po,
            "disable_rounded_total": "1",
            "items": []
        })

        # Recuperar vendedor asignado al cliente
        sales_person = frappe.db.get_value("Sales Team", {"parenttype": "Customer", "parent": cliente}, "sales_person")

        if sales_person:
            sales_order.append("sales_team", {
                "sales_person": sales_person,
                "allocated_percentage": 100
            })

        # Obtener impuestos dinámicos
        impuestos = asignar_impuestos()
        sales_order.taxes_and_charges = impuestos.get("taxes_and_charges")
        sales_order.set("taxes", [])
        for imp in impuestos.get("taxes", []):
            sales_order.append("taxes", {
                "charge_type": imp["charge_type"],
                "account_head": imp["account_head"],
                "description": imp["description"],
                "rate": imp["rate"],
                "included_in_print_rate": imp["included_in_print_rate"],
                "tax_amount": 0
            })

        # Construir payload limpio para logging
        sales_order_payload = {
            "customer": cliente,
            "po_no": numero_po,
            "transaction_date": frappe.utils.nowdate(),
            "delivery_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
            "po_date": po_date,
            "selling_price_list": price_list,
            "disable_rounded_total": "1",
            "items": [],
            "taxes_and_charges": impuestos.get("taxes_and_charges"),
            "taxes": impuestos.get("taxes")
        }

        # Agregar items al documento y al payload
        for item_json in ov["items"]:
            item_code = frappe.db.get_value("Item", {"custom_ean": item_json["ean"]}, "item_code")
            if not item_code:
                frappe.log_error(f"Item {item_json['ean']} no encontrado", "Import OV")
                continue

            precio_lista = frappe.db.get_value(
                "Item Price",
                {"item_code": item_code, "price_list": price_list},
                "price_list_rate"
            )
            if precio_lista is None:
                frappe.log_error(f"Precio no encontrado para {item_code} en {price_list}", "Import OV")
                continue

            almacen = asignar_almacen(item_code) or frappe.db.get_value("Warehouse", {"is_group": 0}, "name")
            precio = calcular_precio_negociado(item_code, item_json["unitPrice"])
            diferencia = precio - precio_lista

            # Agregar item al Sales Order
            sales_order.append("items", {
                "item_code": item_code,
                "qty": item_json["quantity"],
                "rate": precio_lista,
                "warehouse": almacen,
                "custom_precio_walmart": precio,
                "custom_diferencia_precio": diferencia
            })

            # Agregar item al payload limpio
            sales_order_payload["items"].append({
                "item_code": item_code,
                "qty": item_json["quantity"],
                "rate": precio_lista,
                "warehouse": almacen,
                "custom_precio_walmart": precio,
                "custom_diferencia_precio": diferencia
            })

        # Calcular valores obligatorios antes de insert
        sales_order.set_missing_values()
        sales_order.calculate_taxes_and_totals()

        print(f"Insertando orden {numero_po} con {len(sales_order.items)} items")
        try:
            sales_order.insert(ignore_permissions=True)
            frappe.db.commit()
            print(f"Orden {numero_po} insertada correctamente")
        except Exception as e:
            frappe.log_error(f"Error insertando OV {numero_po}: {str(e)}", "Import OV")
            print(f"Error insertando orden {numero_po}: {e}")
            continue

        # Guardar solo el payload construido
        ordenes_json.append(sales_order_payload)

    # Imprimir JSON limpio de todas las órdenes procesadas
    import json
    print(json.dumps(ordenes_json, indent=4))

    return {
        "total": len(ordenes),
        "data": ordenes_json
    }



def calcular_precio_negociado(item_code, unit_price): 

    # Buscar descuento en el Doctype "Descuentos Negociados"
    descuento = frappe.db.get_value("Campos descuentos", {"itemcode": item_code}, "descuento")
    iva = frappe.db.get_value("Campos descuentos", {"itemcode": item_code}, "iva")

    if iva is None:
        iva = 0

    iva = 1 + (iva / 100)
      # Aplicar IVA (12%)
    precio_con_iva = unit_price * iva

    # Si no hay descuento, retornar precio con IVA
    if descuento is None:
        return precio_con_iva

    # Aplicar descuento
    precio_final = precio_con_iva * (1 - (descuento / 100))
    return precio_final

def asignar_almacen(item_code):
    # Buscar almacén específico del ítem
    almacen = frappe.db.get_value("Campos descuentos", {"itemcode": item_code}, "almacen")

    if not almacen:
        # Si no hay, buscar el valor por defecto en Descuentos Negociados
        almacen = frappe.db.get_single_value("Descuentos Negociados", "almacen_defecto")

        if not almacen:
            frappe.logger().warning(
                f"⚠️ No se encontró almacén ni valor por defecto para el item {item_code}"
            )
            return None

    return almacen


def asignar_impuestos():
    plantilla = frappe.db.get_single_value("Descuentos Negociados", "iva")
    payload = {"taxes_and_charges": plantilla, "taxes": []}
    if plantilla:
        template_doc = frappe.get_doc("Sales Taxes and Charges Template", plantilla)
        for idx, imp in enumerate(template_doc.taxes, start=1):
            payload["taxes"].append({
                "idx": idx,
                "charge_type": imp.charge_type,
                "account_head": imp.account_head,
                "description": imp.description,
                "rate": imp.rate,
                "included_in_print_rate": imp.included_in_print_rate,
                "tax_amount": 0  # se recalcula
            })
    return payload