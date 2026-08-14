# Copyright (c) 2025, Maynor Vasquez and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from redis.exceptions import LockError

from edinova.api.erpnext_orden_venta import descargar_ordenes, procesar_ordenes

logger = frappe.logger("edinova", allow_site=True, file_count=10)

LOCK_KEY = "edinova:sync_lock"
LOCK_OWNER_KEY = "edinova:sync_lock_owner"
LOCK_TTL_SEC = 1800  # 30 minutos


class EdinovaSincronizarOrdenesdeVenta(Document):
    def onload(self):
        if self.status == "En proceso" and self._es_estancado():
            self._marcar_estancado()
        _sweep_stale_syncs(exclude=self.name)

    def _es_estancado(self) -> bool:
        if not self.modified:
            return False
        cutoff = frappe.utils.add_to_date(None, seconds=-LOCK_TTL_SEC)
        return frappe.utils.get_datetime(self.modified) < frappe.utils.get_datetime(cutoff)

    def _marcar_estancado(self) -> None:
        msg = (
            f"Sincronización abandonada: el worker no reportó fin en más de "
            f"{LOCK_TTL_SEC // 60} min (probable reinicio del bench, OOM o crash)."
        )
        self.db_set("status", "Error", update_modified=False)
        self.db_set("error", msg, update_modified=False)
        logger.warning(f"Sweep onload: {self.name} marcado como Error por timeout")

    def after_insert(self):
        if not self.fecha_inicio:
            return

        self.db_set("status", "En proceso", update_modified=False)
        frappe.enqueue(
            "edinova.edinova.doctype.edinova_sincronizar_ordenes_de_venta."
            "edinova_sincronizar_ordenes_de_venta.run_sync",
            queue="long",
            timeout=1500,
            docname=self.name,
            fecha_inicio=str(self.fecha_inicio),
            triggered_by=frappe.session.user,
            enqueue_after_commit=True,
        )


def _sweep_stale_syncs(exclude: str | None = None) -> int:
    """Marca como 'Error' los docs en 'En proceso' cuyo modified es más viejo que LOCK_TTL_SEC.

    Limpieza oportunista invocada desde onload y _adquirir_lock — evita necesitar un cron
    dedicado: el barrido solo corre cuando hay actividad real del usuario o un sync nuevo
    arranca. La query es indexada y la mayor parte del tiempo retorna 0 filas.
    """
    cutoff = frappe.utils.add_to_date(None, seconds=-LOCK_TTL_SEC)
    filters = {"status": "En proceso", "modified": ["<", cutoff]}
    if exclude:
        filters["name"] = ["!=", exclude]
    stale = frappe.get_all(
        "Edinova Sincronizar Ordenes de Venta",
        filters=filters,
        pluck="name",
    )
    if not stale:
        return 0
    msg = (
        f"Sincronización abandonada: el worker no reportó fin en más de "
        f"{LOCK_TTL_SEC // 60} min (probable reinicio del bench, OOM o crash)."
    )
    for name in stale:
        frappe.db.set_value(
            "Edinova Sincronizar Ordenes de Venta",
            name,
            {"status": "Error", "error": msg},
            update_modified=False,
        )
        logger.warning(f"Sweep: {name} marcado como Error por timeout de worker")
    return len(stale)


def _adquirir_lock(docname: str) -> tuple[object | None, str | None]:
    """Acquire the global sync lock atomically and return lock plus current owner."""
    _sweep_stale_syncs(exclude=docname)
    cache = frappe.cache()
    lock = cache.lock(
        cache.make_key(LOCK_KEY),
        timeout=LOCK_TTL_SEC,
        blocking=False,
    )
    if not lock.acquire(blocking=False):
        owner = cache.get_value(LOCK_OWNER_KEY, expires=True)
        owner_str = owner.decode() if isinstance(owner, bytes) else str(owner or "")
        return None, owner_str or "desconocido"

    cache.set_value(LOCK_OWNER_KEY, docname, expires_in_sec=LOCK_TTL_SEC)
    return lock, None


def _liberar_lock(lock: object | None, docname: str) -> None:
    if lock is None:
        return
    cache = frappe.cache()
    try:
        lock.release()
    except LockError:
        logger.warning(f"El lock de sincronización de '{docname}' ya no pertenece al job")
    owner = cache.get_value(LOCK_OWNER_KEY, expires=True)
    owner_str = owner.decode() if isinstance(owner, bytes) else str(owner or "")
    if owner_str == docname:
        cache.delete_value(LOCK_OWNER_KEY)


def run_sync(docname: str, fecha_inicio: str, triggered_by: str | None = None) -> None:
    """Orquesta la sincronización en background.

    Pasos: lock → descargar payload → persistir payload (incluso si todo lo demás
    falla) → procesar atómicamente → guardar resultado.
    """
    doc = frappe.get_doc("Edinova Sincronizar Ordenes de Venta", docname)

    lock, dueno = _adquirir_lock(docname)
    if dueno:
        doc.status = "Bloqueado"
        doc.error = (
            f"Otra sincronización está en curso (job '{dueno}'). "
            f"Espere a que termine o, si está colgado, elimine la clave "
            f"'{LOCK_KEY}' del cache."
        )
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        logger.warning(f"Sync {docname} abortado: lock tomado por {dueno}")
        return

    try:
        try:
            ordenes_raw = descargar_ordenes(fecha_inicio)
            doc.payload_original = frappe.as_json(ordenes_raw)
            doc.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            doc.status = "Error"
            doc.error = (
                "Falló la descarga del API antes de poder guardar el payload:\n"
                + frappe.get_traceback()
            )
            frappe.log_error(doc.error, f"Edinova Sync {docname}")
            logger.exception(f"Sync {docname}: error descargando")
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            return

        try:
            resultado = procesar_ordenes(ordenes_raw)
            doc.status = "Exitoso"
            doc.total_registros = resultado.get("total", 0)
            doc.detalle = frappe.as_json(
                resultado.get("data", {}),
                indent=2,
                ensure_ascii=False,
            )
            doc.error = None
        except Exception:
            doc.status = "Error"
            doc.error = frappe.get_traceback()
            frappe.log_error(doc.error, f"Edinova Sync {docname}")
            logger.exception(f"Sync {docname}: error procesando")

        doc.save(ignore_permissions=True)
        frappe.db.commit()

    finally:
        _liberar_lock(lock, docname)
        # Publicamos al room del usuario (auto-joined al conectar el socket) en vez
        # del room del doc, que requiere un doc_subscribe asíncrono y puede
        # perderse si el sync termina antes de que el cliente se suscriba.
        target_user = triggered_by or doc.owner
        frappe.publish_realtime(
            "edinova_sync_done",
            {"name": docname, "status": doc.status},
            user=target_user,
            after_commit=True,
        )
