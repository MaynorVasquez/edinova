from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import frappe

from .edinova_orden_venta import get_ordenes_venta

logger = frappe.logger("edinova", allow_site=True, file_count=10)

PRECISION_PRECIO = Decimal("0.000001")


class EdinovaSyncError(frappe.ValidationError):
    """Error de pre-validación que aborta la creación de cualquier Sales Order."""
    pass


def validar_cantidades(ordenes: list[dict]) -> list[dict]:
    """Compara la cantidad esperada por EAN contra la cantidad distribuida en cada PO."""
    result = []

    for order in ordenes:
        dist_qty: dict[str, int] = defaultdict(int)
        for dist in order["merchandisedistribution"]:
            for item in dist["items"]:
                dist_qty[item["ean"]] += item["quantity"]

        order_result = {
            "po": order["purchaseOrderNumber"]["po"],
            "items": [],
        }

        for item in order["items"]:
            ean = item["ean"]
            expected = item["quantity"]
            actual = dist_qty.get(ean, 0)
            order_result["items"].append({
                "ean": ean,
                "intCode": item["intCode"],
                "expectedQuantity": expected,
                "distributedQuantity": actual,
                "difference": expected - actual,
                "quantity": expected - actual,
                "match": expected == actual,
            })

        result.append(order_result)

    return result


def _cargar_descuentos_indexados(item_codes: list[str]) -> dict[str, dict]:
    """Pre-carga descuento/iva/almacen por item_code en una sola consulta."""
    if not item_codes:
        return {}

    rows = frappe.get_all(
        "Campos descuentos",
        filters={"itemcode": ["in", item_codes]},
        fields=["itemcode", "descuento", "iva", "almacen"],
    )
    return {r["itemcode"]: r for r in rows}


def _cargar_items_por_ean(eans: list[str]) -> dict[str, str]:
    """Mapa EAN → item_code activo; rechaza asociaciones ambiguas.

    El API de Walmart puede mandar el EAN como número; en BD el campo
    ``custom_ean`` es Data (string). Normalizamos ambos lados a str para
    evitar mismatches por tipo.
    """
    if not eans:
        return {}

    eans_str = [str(e) for e in eans]
    rows = frappe.get_all(
        "Item",
        filters={"custom_ean": ["in", eans_str], "disabled": 0},
        fields=["name", "custom_ean", "disabled"],
    )
    items_by_ean: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if not row.get("disabled"):
            items_by_ean[str(row["custom_ean"])].append(row["name"])

    duplicates = {
        ean: sorted(item_codes)
        for ean, item_codes in items_by_ean.items()
        if len(item_codes) > 1
    }
    if duplicates:
        detail = "; ".join(
            f"EAN {ean}: {', '.join(item_codes)}"
            for ean, item_codes in sorted(duplicates.items())
        )
        raise EdinovaSyncError(
            "Existen EAN asociados a más de un Item activo. "
            f"Corrija los datos maestros antes de sincronizar: {detail}."
        )

    return {ean: item_codes[0] for ean, item_codes in items_by_ean.items()}


def _cargar_precios(item_codes: list[str], price_list: str) -> dict[str, float]:
    """Mapa item_code → price_list_rate en una sola consulta."""
    if not item_codes or not price_list:
        return {}

    rows = frappe.get_all(
        "Item Price",
        filters={"item_code": ["in", item_codes], "price_list": price_list},
        fields=["item_code", "price_list_rate"],
    )
    return {r["item_code"]: r["price_list_rate"] for r in rows}


def save_order(
    ov: dict,
    gln_item: dict,
    ordenes_json: list,
    contexto: dict,
) -> None:
    """Crea una Sales Order a partir de una OV + un GLN. ``contexto`` contiene
    los datos pre-cargados (cliente, price_list, company, impuestos, dias_entrega,
    descuentos, items_por_ean, precios)."""
    numero_po = ov["purchaseOrderNumber"]["po"]
    gln = gln_item["gln"]
    cliente = contexto["cliente"]

    logger.info(f"Procesando orden {numero_po} con GLN {gln}")

    date_str = str(ov["purchaseOrderNumber"]["datePO"])
    dt = datetime.strptime(date_str, "%Y%m%d%H%M")
    po_date = dt.strftime("%Y-%m-%d")

    if _existe_orden_para_gln(numero_po, gln, cliente):
        logger.info(f"Orden {numero_po} con GLN {gln} ya existe, saltando")
        return

    price_list = contexto["price_list"]
    company = contexto["company"]
    dias_entrega = contexto["dias_entrega"]

    if not gln_item.get("items"):
        logger.info(f"Orden {numero_po} con GLN {gln} no tiene items, saltando")
        return

    sales_order = frappe.get_doc({
        "doctype": "Sales Order",
        "customer": cliente,
        "company": company,
        "transaction_date": frappe.utils.nowdate(),
        "delivery_date": frappe.utils.add_days(frappe.utils.nowdate(), dias_entrega),
        "po_date": po_date,
        "selling_price_list": price_list,
        "po_no": numero_po,
        "custom_gln": gln,
        "disable_rounded_total": "1",
        "items": [],
    })

    sales_person = frappe.db.get_value(
        "Sales Team",
        {"parenttype": "Customer", "parent": cliente},
        "sales_person",
    )
    if sales_person:
        sales_order.append("sales_team", {
            "sales_person": sales_person,
            "allocated_percentage": 100,
        })

    impuestos = contexto["impuestos"]
    sales_order.taxes_and_charges = impuestos.get("taxes_and_charges")
    sales_order.set("taxes", [])
    for imp in impuestos.get("taxes", []):
        sales_order.append("taxes", {
            "charge_type": imp["charge_type"],
            "account_head": imp["account_head"],
            "description": imp["description"],
            "rate": imp["rate"],
            "included_in_print_rate": imp["included_in_print_rate"],
            "tax_amount": 0,
        })

    sales_order_payload = {
        "customer": cliente,
        "po_no": numero_po,
        "custom_gln": gln,
        "transaction_date": frappe.utils.nowdate(),
        "delivery_date": frappe.utils.add_days(frappe.utils.nowdate(), dias_entrega),
        "po_date": po_date,
        "selling_price_list": price_list,
        "disable_rounded_total": "1",
        "items": [],
        "taxes_and_charges": impuestos.get("taxes_and_charges"),
        "taxes": impuestos.get("taxes"),
    }

    items_por_ean = contexto["items_por_ean"]
    precios = contexto["precios"]
    descuentos = contexto["descuentos"]
    almacen_defecto = contexto["almacen_defecto"]
    unit_price_por_ean = {
        str(item["ean"]): item.get("unitPrice", 0) for item in ov["items"]
    }

    for item_json in gln_item["items"]:
        ean = str(item_json["ean"])
        item_code = items_por_ean.get(ean)
        if not item_code:
            raise EdinovaSyncError(
                f"PO {numero_po}: no se encontró ningún Item con "
                f"custom_ean = '{ean}' (doctype 'Item', campo 'custom_ean')."
            )

        precio_lista = precios.get(item_code)
        if precio_lista is None:
            raise EdinovaSyncError(
                f"PO {numero_po}: item '{item_code}' sin precio en la lista "
                f"'{price_list}' (doctype 'Item Price')."
            )

        unit_precio = unit_price_por_ean.get(ean) or 0
        almacen = (descuentos.get(item_code, {}).get("almacen") or almacen_defecto)
        precio = calcular_precio_negociado(item_code, unit_precio, descuentos)
        diferencia = _redondear_decimal(
            _decimal(precio, "precio negociado")
            - _decimal(precio_lista, "precio de lista")
        )
        quantity = item_json["quantity"]

        sales_order.append("items", {
            "item_code": item_code,
            "qty": quantity,
            "rate": precio_lista,
            "warehouse": almacen,
            "custom_precio_walmart": precio,
            "custom_diferencia_precio": diferencia,
        })
        sales_order_payload["items"].append({
            "item_code": item_code,
            "qty": quantity,
            "rate": precio_lista,
            "warehouse": almacen,
            "custom_precio_walmart": precio,
            "custom_diferencia_precio": diferencia,
        })

    sales_order.set_missing_values()
    sales_order.calculate_taxes_and_totals()

    logger.info(f"Insertando orden {numero_po} con {len(sales_order.items)} items")
    sales_order.insert(ignore_permissions=True)
    ordenes_json.append(sales_order_payload)


def _existe_orden_para_gln(numero_po: str, gln: str, cliente: str) -> bool:
    """Use PO + GLN as the idempotency key while keeping customers isolated."""
    return bool(
        frappe.db.exists(
            "Sales Order",
            {
                "po_no": numero_po,
                "custom_gln": gln,
                "customer": cliente,
            },
        )
    )


def _cargar_configuracion() -> dict[str, Any]:
    """Carga sólo la configuración (Descuentos Negociados, Customer, Taxes).

    No hace queries de items. Devuelve un dict que puede tener valores ``None``;
    la validación se hace después en ``_validar_configuracion``.
    """
    cliente = frappe.db.get_single_value("Descuentos Negociados", "cardcode")
    price_list = (
        frappe.db.get_value("Customer", cliente, "default_price_list")
        if cliente else None
    )
    company = frappe.db.get_single_value("Descuentos Negociados", "company")
    almacen_defecto = (
        frappe.db.get_single_value("Descuentos Negociados", "almacen_defecto")
        or frappe.db.get_value("Warehouse", {"is_group": 0}, "name")
    )
    dias_entrega = frappe.db.get_single_value(
        "Descuentos Negociados", "dias_entrega"
    )
    if dias_entrega is None:
        dias_entrega = 7

    return {
        "cliente": cliente,
        "price_list": price_list,
        "company": company,
        "almacen_defecto": almacen_defecto,
        "dias_entrega": int(dias_entrega) if dias_entrega is not None else 0,
        "impuestos": asignar_impuestos(),
    }


def _agregar_lookups(ordenes: list[dict], contexto: dict[str, Any]) -> dict[str, Any]:
    """Pre-carga items_por_ean, precios y descuentos en el contexto."""
    eans: set[str] = set()
    for ov in ordenes:
        for item in ov.get("items", []):
            eans.add(str(item["ean"]))
        for dist in ov.get("merchandisedistribution", []):
            for item in dist.get("items", []):
                eans.add(str(item["ean"]))

    items_por_ean = _cargar_items_por_ean(list(eans))
    item_codes = list(items_por_ean.values())

    return {
        **contexto,
        "items_por_ean": items_por_ean,
        "descuentos": _cargar_descuentos_indexados(item_codes),
        "precios": _cargar_precios(item_codes, contexto.get("price_list")),
    }


def _validar_configuracion(contexto: dict[str, Any]) -> list[str]:
    """Verifica que la configuración necesaria esté presente y consistente.

    Devuelve lista de mensajes de error accionables. Lista vacía = OK.
    """
    errores: list[str] = []

    if not contexto.get("cliente"):
        errores.append(
            "Cliente no configurado. Defínalo en 'Descuentos Negociados' → 'Cliente'."
        )
        return errores  # sin cliente los demás chequeos no aportan

    if not contexto.get("price_list"):
        errores.append(
            f"El cliente '{contexto['cliente']}' no tiene 'Default Price List' "
            f"configurada (doctype 'Customer', campo 'default_price_list')."
        )

    if not contexto.get("company"):
        errores.append(
            "Empresa no configurada. Defínala en 'Descuentos Negociados' → 'Empresa'."
        )

    if not contexto.get("almacen_defecto"):
        errores.append(
            "No se encontró un almacén por defecto. Configure "
            "'Descuentos Negociados' → 'Almacén por defecto', o asegúrese de que "
            "exista al menos un Warehouse de tipo no-grupo."
        )

    dias = contexto.get("dias_entrega")
    if not isinstance(dias, int) or dias < 0:
        errores.append(
            f"'Días de entrega' debe ser un entero ≥ 0 (valor actual: {dias!r}). "
            f"Configúrelo en 'Descuentos Negociados' → 'Días de entrega'."
        )

    impuestos = contexto.get("impuestos") or {}
    template = impuestos.get("taxes_and_charges")
    taxes_list = impuestos.get("taxes") or []
    if not template:
        errores.append(
            "Plantilla de impuestos no configurada. Defínala en "
            "'Descuentos Negociados' → 'Impuesto por defecto'."
        )
    elif not taxes_list:
        errores.append(
            f"La plantilla de impuestos '{template}' no tiene líneas de impuestos. "
            f"Revise el doctype 'Sales Taxes and Charges Template' → '{template}'."
        )

    return errores


def _normalizar_orden(ov: dict, idx: int) -> dict:
    """Normaliza los tipos de una OV recibida del API y valida el schema mínimo.

    Convierte EAN e intCode a str, quantity a int, unitPrice a float, y valida
    el formato de ``datePO``. Levanta ``EdinovaSyncError`` con un mensaje
    específico (orden + campo + valor recibido) si algo no es convertible o
    falta un campo requerido.
    """
    ctx = f"orden #{idx + 1}"

    pon = ov.get("purchaseOrderNumber") or {}
    if not pon.get("po"):
        raise EdinovaSyncError(f"{ctx}: falta 'purchaseOrderNumber.po'")
    if not pon.get("datePO"):
        raise EdinovaSyncError(f"{ctx}: falta 'purchaseOrderNumber.datePO'")

    pon["po"] = str(pon["po"])
    date_str = str(pon["datePO"])
    try:
        datetime.strptime(date_str, "%Y%m%d%H%M")
    except ValueError as e:
        raise EdinovaSyncError(
            f"{ctx} (PO {pon['po']}): formato inválido en datePO='{date_str}', "
            f"se esperaba YYYYMMDDHHMM ({e})"
        ) from e
    pon["datePO"] = date_str
    ov["purchaseOrderNumber"] = pon

    items = ov.get("items") or []
    if not items:
        raise EdinovaSyncError(f"{ctx} (PO {pon['po']}): la orden no tiene items")

    order_eans: set[str] = set()
    for j, item in enumerate(items):
        ictx = f"{ctx} (PO {pon['po']}) item #{j + 1}"
        if "ean" not in item or item.get("ean") in (None, ""):
            raise EdinovaSyncError(f"{ictx}: falta 'ean'")
        item["ean"] = str(item["ean"])
        if item["ean"] in order_eans:
            raise EdinovaSyncError(
                f"{ictx}: EAN duplicado '{item['ean']}' en los items de la orden."
            )
        order_eans.add(item["ean"])
        item["intCode"] = str(item.get("intCode") or "")
        item["quantity"] = _entero_positivo(
            item.get("quantity"),
            f"{ictx} (EAN {item['ean']}) quantity",
        )
        try:
            item["unitPrice"] = float(item.get("unitPrice") or 0)
        except (TypeError, ValueError) as e:
            raise EdinovaSyncError(
                f"{ictx} (EAN {item['ean']}): 'unitPrice' no convertible a float "
                f"(valor recibido: {item.get('unitPrice')!r})"
            ) from e

    dists = ov.get("merchandisedistribution") or []
    for k, dist in enumerate(dists):
        dctx = f"{ctx} (PO {pon['po']}) merchandisedistribution #{k + 1}"
        dist["gln"] = str(dist.get("gln") or "")
        dist["glnname"] = str(dist.get("glnname") or "")
        distribution_eans: set[str] = set()
        for m, ditem in enumerate(dist.get("items") or []):
            mctx = f"{dctx} item #{m + 1}"
            if "ean" not in ditem or ditem.get("ean") in (None, ""):
                raise EdinovaSyncError(f"{mctx}: falta 'ean'")
            ditem["ean"] = str(ditem["ean"])
            if ditem["ean"] in distribution_eans:
                raise EdinovaSyncError(
                    f"{mctx}: EAN duplicado '{ditem['ean']}' dentro del mismo GLN."
                )
            distribution_eans.add(ditem["ean"])
            ditem["quantity"] = _entero_positivo(
                ditem.get("quantity"),
                f"{mctx} (EAN {ditem['ean']}) quantity",
            )
        dist["items"] = dist.get("items") or []
    ov["merchandisedistribution"] = dists

    return ov


def _pre_validar(ordenes: list[dict], contexto: dict[str, Any]) -> list[str]:
    """Recorre las órdenes y reporta errores de datos sin tocar la BD.

    Verifica que cada EAN exista como Item en ERPNext y tenga precio en la lista
    correspondiente. Si esta función devuelve errores, ninguna Sales Order
    debe crearse.
    """
    items_por_ean = contexto["items_por_ean"]
    precios = contexto["precios"]
    price_list = contexto["price_list"]
    errores: list[str] = []

    for ov in ordenes:
        numero_po = ov["purchaseOrderNumber"]["po"]
        eans_revisados: set[str] = set()

        for gln_item in ov.get("merchandisedistribution", []):
            for item_json in gln_item.get("items", []) or []:
                ean = str(item_json["ean"])
                if ean in eans_revisados:
                    continue
                eans_revisados.add(ean)

                item_code = items_por_ean.get(ean)
                if not item_code:
                    errores.append(
                        f"PO {numero_po}: no se encontró ningún Item con "
                        f"custom_ean = '{ean}'. "
                        f"Buscado en doctype 'Item', campo 'custom_ean'. "
                        f"Verifique que el Item exista y que tenga el EAN "
                        f"cargado en ese campo."
                    )
                    continue
                if precios.get(item_code) is None:
                    errores.append(
                        f"PO {numero_po}: item '{item_code}' (EAN {ean}) "
                        f"sin precio en la lista '{price_list}'. "
                        f"Buscado en doctype 'Item Price' con filtro "
                        f"item_code='{item_code}' y price_list='{price_list}'."
                    )

    return errores


def descargar_ordenes(fecha: str) -> list[dict]:
    """Trae las órdenes crudas del API de Walmart sin procesarlas."""
    ordenes = get_ordenes_venta(fecha)
    logger.info(f"Órdenes recibidas del API: {len(ordenes)}")
    return ordenes


def procesar_ordenes(ordenes_raw: list[dict]) -> dict[str, Any]:
    """Toma órdenes ya descargadas, las normaliza, valida y crea las Sales Order
    de forma atómica. Si algo falla, no queda ninguna OV creada.
    """
    ordenes = [_normalizar_orden(ov, i) for i, ov in enumerate(ordenes_raw)]

    data_validacion = validar_cantidades(ordenes)

    contexto = _cargar_configuracion()
    errores_cfg = _validar_configuracion(contexto)
    if errores_cfg:
        raise EdinovaSyncError(
            "Configuración inválida; no se procesa ninguna orden:\n- "
            + "\n- ".join(errores_cfg)
        )

    contexto = _agregar_lookups(ordenes, contexto)

    errores_pre = _pre_validar(ordenes, contexto)
    if errores_pre:
        raise EdinovaSyncError(
            "No se procesa ninguna orden por errores de validación:\n- "
            + "\n- ".join(errores_pre)
        )

    ordenes_json: list[dict] = []
    savepoint = "edinova_sync"
    frappe.db.savepoint(savepoint)
    try:
        for ov in ordenes:
            for gln_item in ov["merchandisedistribution"]:
                save_order(ov, gln_item, ordenes_json, contexto)

            numero_po = ov["purchaseOrderNumber"]["po"]
            orden_validacion = next(
                (v for v in data_validacion if v["po"] == numero_po), None
            )
            if orden_validacion:
                items_con_diferencia = [
                    item for item in orden_validacion["items"] if item["difference"] > 0
                ]
                if items_con_diferencia:
                    gln_extra = {
                        "gln": "",
                        "glnname": "",
                        "items": items_con_diferencia,
                    }
                    save_order(ov, gln_extra, ordenes_json, contexto)
                    logger.info(
                        f"Generada OV adicional para diferencias en PO {numero_po}"
                    )
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        logger.exception("Error durante la sincronización: rollback de todas las OV")
        raise

    frappe.db.commit()

    return {
        "total": len(ordenes_json),
        "data": {
            "ordenes": ordenes_json,
            "validacion": data_validacion,
        },
    }


def crear_ordenes_venta(fecha: str) -> dict[str, Any]:
    """Punto de entrada legacy: descarga + procesa en una sola llamada."""
    return procesar_ordenes(descargar_ordenes(fecha))


def calcular_precio_negociado(
    item_code: str,
    unit_price: float,
    descuentos: dict[str, dict] | None = None,
) -> float:
    """Aplica IVA y descuento configurados al precio unitario.

    Si ``descuentos`` se pasa pre-cargado evita un round-trip a la BD.
    """
    if descuentos is not None:
        row = descuentos.get(item_code, {})
        descuento = row.get("descuento")
        iva = row.get("iva")
    else:
        descuento = frappe.db.get_value(
            "Campos descuentos", {"itemcode": item_code}, "descuento"
        )
        iva = frappe.db.get_value(
            "Campos descuentos", {"itemcode": item_code}, "iva"
        )

    precio_decimal = _decimal(unit_price, f"precio unitario del item '{item_code}'")
    iva_decimal = _decimal(iva or 0, f"IVA del item '{item_code}'")
    descuento_decimal = _decimal(
        descuento or 0, f"descuento del item '{item_code}'"
    )

    if precio_decimal < 0:
        raise EdinovaSyncError(
            f"El precio unitario del item '{item_code}' no puede ser negativo."
        )
    for label, value in (("IVA", iva_decimal), ("descuento", descuento_decimal)):
        if value < 0 or value > 100:
            raise EdinovaSyncError(
                f"El {label} del item '{item_code}' debe estar entre 0 y 100."
            )

    precio_con_iva = precio_decimal * (Decimal("1") + iva_decimal / Decimal("100"))
    precio_final = precio_con_iva * (
        Decimal("1") - descuento_decimal / Decimal("100")
    )
    return _redondear_decimal(precio_final)


def _decimal(value: Any, field_label: str) -> Decimal:
    """Convert an external numeric value without inheriting binary float noise."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EdinovaSyncError(
            f"{field_label.capitalize()} tiene un valor numérico inválido: {value!r}."
        ) from exc
    if not result.is_finite():
        raise EdinovaSyncError(
            f"{field_label.capitalize()} tiene un valor numérico inválido: {value!r}."
        )
    return result


def _entero_positivo(value: Any, field_label: str) -> int:
    quantity = _decimal(value, field_label)
    if quantity != quantity.to_integral_value():
        raise EdinovaSyncError(
            f"{field_label.capitalize()} debe ser un entero; valor recibido: {value!r}."
        )
    if quantity <= 0:
        raise EdinovaSyncError(
            f"{field_label.capitalize()} debe ser mayor que cero; "
            f"valor recibido: {value!r}."
        )
    return int(quantity)


def _redondear_decimal(value: Decimal) -> float:
    return float(value.quantize(PRECISION_PRECIO, rounding=ROUND_HALF_UP))


def asignar_almacen(item_code: str) -> str | None:
    """Devuelve el almacén configurado para el item, o el default global."""
    almacen = frappe.db.get_value(
        "Campos descuentos", {"itemcode": item_code}, "almacen"
    )

    if not almacen:
        almacen = frappe.db.get_single_value("Descuentos Negociados", "almacen_defecto")
        if not almacen:
            logger.warning(
                f"No se encontró almacén ni valor por defecto para el item {item_code}"
            )
            return None

    return almacen


def asignar_impuestos() -> dict[str, Any]:
    """Carga la plantilla de impuestos por defecto y la traduce al formato de Sales Order."""
    plantilla = frappe.db.get_single_value("Descuentos Negociados", "iva")
    payload: dict[str, Any] = {"taxes_and_charges": plantilla, "taxes": []}
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
                "tax_amount": 0,
            })
    return payload
