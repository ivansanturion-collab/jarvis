"""Cliente de Asana con auto-discovery de GIDs."""

import json
import hashlib
from datetime import datetime, timedelta, date, timezone

import asana
from pathlib import Path
from .config import (
    ASANA_ACCESS_TOKEN,
    ASANA_PROJECT_GID,
    ASANA_IDS_FILE,
    ASANA_WORKSPACE_GID,
    PROCESADOS_FILE,
    PRIORIDAD_SECCION_MAP,
    logger,
)


class AsanaClient:
    def __init__(self):
        # SDK v5: usar Configuration + ApiClient y APIs específicas
        configuration = asana.Configuration()
        configuration.access_token = ASANA_ACCESS_TOKEN
        api_client = asana.ApiClient(configuration)

        self.tasks_api = asana.TasksApi(api_client)
        self.sections_api = asana.SectionsApi(api_client)
        self.projects_api = asana.ProjectsApi(api_client)
        self.users_api = asana.UsersApi(api_client)

        self.ids = self._load_or_discover_ids()
        self._init_procesados()

    def _resolver_seccion_gid_por_nombre_corto(self, nombre_corto: str) -> str | None:
        """Resuelve el GID de una sección a partir de un nombre simple ("Hoy", "Semana")."""
        secciones = self.ids.get("secciones", {}) or {}

        # Match exacto
        gid = secciones.get(nombre_corto)
        if gid:
            return gid

        # Match por sufijo, para nombres con emoji como "🔥 Hoy"
        for nombre_seccion, sgid in secciones.items():
            if nombre_seccion.endswith(f" {nombre_corto}"):
                return sgid

        logger.warning(f"⚠️ No se encontró sección en Asana para nombre '{nombre_corto}'")
        return None
    # ──────────────────────────────────────────────
    # Auto-discovery de IDs
    # ──────────────────────────────────────────────

    def _load_or_discover_ids(self) -> dict:
        """Carga IDs cacheados o los descubre via API."""
        if ASANA_IDS_FILE.exists():
            ids = json.loads(ASANA_IDS_FILE.read_text(encoding="utf-8"))
            logger.info("✅ IDs de Asana cargados desde cache")

            # Migración: asegurar que exista owner_user_gid
            if not ids.get("owner_user_gid"):
                try:
                    ids = self._descubrir_owner_user_gid(ids)
                    ASANA_IDS_FILE.write_text(
                        json.dumps(ids, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    logger.info("✅ owner_user_gid agregado a asana_ids.json")
                except Exception as e:
                    logger.error(f"❌ No se pudo actualizar owner_user_gid desde cache: {e}")

            return ids

        logger.info("🔍 Descubriendo IDs de Asana via API...")
        ids = self.discover_asana_ids()
        ASANA_IDS_FILE.write_text(
            json.dumps(ids, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"✅ IDs descubiertos y guardados en {ASANA_IDS_FILE}")
        return ids

    def _descubrir_owner_user_gid(self, ids: dict) -> dict:
        """Completa owner_user_gid en el dict de IDs."""
        try:
            me = self.users_api.get_user(
                "me",
                {
                    "opt_fields": "gid,name,email,workspaces",
                },
            )
            workspaces = me.get("workspaces", [])
            if any(ws.get("gid") == ASANA_WORKSPACE_GID for ws in workspaces):
                ids["owner_user_gid"] = me["gid"]
                logger.info(
                    f"  Owner detectado para workspace {ASANA_WORKSPACE_GID}: "
                    f"{me.get('name')} ({me.get('email', 'sin email')}) → {me['gid']}"
                )
            else:
                ids["owner_user_gid"] = me["gid"]
                logger.warning(
                    "⚠️ Usuario 'me' no parece pertenecer al workspace configurado, "
                    "pero se usará igualmente como owner por defecto"
                )
        except Exception as e:
            logger.error(f"❌ No se pudo descubrir owner_user_gid: {e}")
        return ids

    def discover_asana_ids(self) -> dict:
        """Descubre secciones, custom fields y user GIDs relevantes."""
        ids = {
            "secciones": {},
            "campo_proyecto_gid": None,
            "opciones_proyecto": {},
            "owner_user_gid": None,
        }

        # 1. Obtener secciones
        secciones = self.sections_api.get_sections_for_project(
            ASANA_PROJECT_GID,
            {
                "opt_fields": "name,gid",
            },
        )
        for seccion in secciones:
            ids["secciones"][seccion["name"]] = seccion["gid"]
            logger.info(f"  Sección: {seccion['name']} → {seccion['gid']}")

        # 2. Obtener custom fields del proyecto
        project = self.projects_api.get_project(
            ASANA_PROJECT_GID,
            {
                "opt_fields": (
                    "custom_field_settings.custom_field.name,"
                    "custom_field_settings.custom_field.gid,"
                    "custom_field_settings.custom_field.enum_options"
                )
            },
        )

        for setting in project.get("custom_field_settings", []):
            cf = setting.get("custom_field", {})
            if cf.get("name") == "Proyecto":
                ids["campo_proyecto_gid"] = cf["gid"]
                logger.info(f"  Campo 'Proyecto' GID: {cf['gid']}")

                for opcion in cf.get("enum_options", []):
                    if opcion.get("enabled", True):
                        ids["opciones_proyecto"][opcion["name"]] = opcion["gid"]
                        logger.info(f"    Opción: {opcion['name']} → {opcion['gid']}")
                break

        if not ids["campo_proyecto_gid"]:
            logger.warning("⚠️ No se encontró el campo 'Proyecto' en Asana")

        # 3. Descubrir user GID del owner del workspace (Ivan)
        ids = self._descubrir_owner_user_gid(ids)

        return ids

    def refresh_ids(self):
        """Fuerza re-discovery de IDs (útil si cambia algo en Asana)."""
        if ASANA_IDS_FILE.exists():
            ASANA_IDS_FILE.unlink()
        self.ids = self._load_or_discover_ids()

    # ──────────────────────────────────────────────
    # Deduplicación
    # ──────────────────────────────────────────────

    def _init_procesados(self):
        """Inicializa archivo de mensajes procesados."""
        if not PROCESADOS_FILE.exists():
            PROCESADOS_FILE.write_text("[]", encoding="utf-8")

    def _ya_procesado(self, message_id: str) -> bool:
        """Verifica si un mensaje ya fue procesado."""
        procesados = json.loads(PROCESADOS_FILE.read_text(encoding="utf-8"))
        return message_id in procesados

    def _marcar_procesado(self, message_id: str):
        """Marca un mensaje como procesado."""
        procesados = json.loads(PROCESADOS_FILE.read_text(encoding="utf-8"))
        procesados.append(message_id)
        PROCESADOS_FILE.write_text(
            json.dumps(procesados, ensure_ascii=False),
            encoding="utf-8",
        )

    # ──────────────────────────────────────────────
    # Crear tarea
    # ──────────────────────────────────────────────

    def crear_tarea(self, texto: str, clasificacion: dict, message_id: str, fuente: str = "telegram") -> dict | None:
        """
        Crea una tarea en Asana Cockpit.
        
        Args:
            texto: Texto original del mensaje
            clasificacion: Output del clasificador GPT
            message_id: ID único del mensaje (para dedup)
            fuente: "telegram" u otro
        
        Returns:
            Task dict de Asana, o None si ya fue procesado
        """
        # Dedup check
        dedup_id = f"{fuente}_{message_id}"
        if self._ya_procesado(dedup_id):
            logger.info(f"⏭️ Mensaje {dedup_id} ya procesado, saltando")
            return None

        # Determinar sección según prioridad
        nombre_seccion = PRIORIDAD_SECCION_MAP.get(
            clasificacion.get("prioridad", "media"), "Semana"
        )
        seccion_gid = self._resolver_seccion_gid_por_nombre_corto(nombre_seccion)

        # Construir notas
        emoji_prioridad = {"alta": "🔴", "media": "🟡", "baja": "🟢"}.get(
            clasificacion.get("prioridad"), "⚪"
        )
        notas = (
            f"Fuente: {fuente}\n"
            f"Tipo: {clasificacion.get('tipo', 'nota')}\n"
            f"Prioridad: {emoji_prioridad} {clasificacion.get('prioridad', 'media')}\n"
            f"Proyecto: {clasificacion.get('proyecto', 'Personal')}\n"
            f"\n---\n\n"
            f"Texto original:\n{texto}"
        )

        # Construir custom fields
        custom_fields = {}
        campo_gid = self.ids.get("campo_proyecto_gid")
        if campo_gid:
            proyecto = clasificacion.get("proyecto", "Personal")
            opciones = self.ids.get("opciones_proyecto", {}) or {}

            # Primero intentamos match exacto
            opcion_gid = opciones.get(proyecto)

            # Si no hay, intentamos hacer match por sufijo (para nombres con emoji como "🎤 Speaker")
            if not opcion_gid:
                for nombre_opcion, gid in opciones.items():
                    if nombre_opcion.endswith(f" {proyecto}"):
                        opcion_gid = gid
                        break

            if opcion_gid:
                custom_fields[campo_gid] = opcion_gid
            else:
                logger.warning(
                    f"⚠️ No se encontró opción de custom field 'Proyecto' para valor '{proyecto}'"
                )

        # ──────────────────────────────────────────────
        # Crear tarea
        # ──────────────────────────────────────────────

        # Crear tarea
        try:
            task_data = {
                "name": clasificacion.get("resumen", texto[:80]),
                "notes": notas,
                "projects": [ASANA_PROJECT_GID],
                "custom_fields": custom_fields,
            }

            # Asignar owner (si lo tenemos cacheado)
            owner_gid = self.ids.get("owner_user_gid")
            if owner_gid:
                task_data["assignee"] = owner_gid

            # Due date (viene del clasificador como YYYY-MM-DD o None)
            due_date = clasificacion.get("due_date")
            if due_date:
                task_data["due_on"] = due_date

            body = {"data": task_data}
            task = self.tasks_api.create_task(body, {})
            logger.info(f"✅ Tarea creada: {task['gid']} - {task['name']}")

            # Mover a sección correcta
            if seccion_gid:
                self.sections_api.add_task_for_section(
                    seccion_gid,
                    {
                        "body": {"data": {"task": task["gid"]}},
                    },
                )
                logger.info(f"  → Movida a sección: {nombre_seccion}")

            # Marcar como procesado
            self._marcar_procesado(dedup_id)

            return task

        except Exception as e:
            logger.error(f"❌ Error creando tarea en Asana: {e}")
            raise

    def actualizar_tarea(self, task_gid: str, clasificacion: dict) -> dict | None:
        """
        Actualiza una tarea existente en Asana usando la clasificación dada.
        """
        # Determinar sección según prioridad
        nombre_seccion = PRIORIDAD_SECCION_MAP.get(
            clasificacion.get("prioridad", "media"), "Semana"
        )
        seccion_gid = self._resolver_seccion_gid_por_nombre_corto(nombre_seccion)

        # Construir custom fields
        custom_fields = {}
        campo_gid = self.ids.get("campo_proyecto_gid")
        if campo_gid:
            proyecto = clasificacion.get("proyecto", "Personal")
            opciones = self.ids.get("opciones_proyecto", {}) or {}

            opcion_gid = opciones.get(proyecto)
            if not opcion_gid:
                for nombre_opcion, gid in opciones.items():
                    if nombre_opcion.endswith(f" {proyecto}"):
                        opcion_gid = gid
                        break

            if opcion_gid:
                custom_fields[campo_gid] = opcion_gid
            else:
                logger.warning(
                    f"⚠️ No se encontró opción de custom field 'Proyecto' para valor '{proyecto}'"
                )

        try:
            # Recuperar notas viejas para preservar el texto original
            existing_task = self.tasks_api.get_task(task_gid, {"opt_fields": "notes"})
            old_notes = existing_task.get("notes", "")

            emoji_prioridad = {"alta": "🔴", "media": "🟡", "baja": "🟢"}.get(
                clasificacion.get("prioridad"), "⚪"
            )

            # Preservar el texto original (lo que está después de "---")
            if "---" in old_notes:
                parts = old_notes.split("---", 1)
                texto_original = "---" + parts[1]
            else:
                texto_original = "\n---\n\nTexto original:\n(sin texto original previo)"

            notas = (
                f"Tipo: {clasificacion.get('tipo', 'nota')}\n"
                f"Prioridad: {emoji_prioridad} {clasificacion.get('prioridad', 'media')}\n"
                f"Proyecto: {clasificacion.get('proyecto', 'Personal')}\n"
                f"\n{texto_original}"
            )

            task_data = {
                "name": clasificacion.get("resumen", "Sin título"),
                "notes": notas,
            }

            if custom_fields:
                task_data["custom_fields"] = custom_fields

            due_date = clasificacion.get("due_date")
            if due_date:
                task_data["due_on"] = due_date
            else:
                task_data["due_on"] = None

            body = {"data": task_data}
            task = self.tasks_api.update_task(body, task_gid, {})
            logger.info(f"✅ Tarea actualizada: {task['gid']} - {task['name']}")

            # Mover a sección correcta si es posible
            if seccion_gid:
                self.sections_api.add_task_for_section(
                    seccion_gid,
                    {
                        "body": {"data": {"task": task["gid"]}},
                    },
                )
                logger.info(f"  → Movida a sección: {nombre_seccion}")

            return task

        except Exception as e:
            logger.error(f"❌ Error actualizando tarea {task_gid} en Asana: {e}")
            raise

    # ──────────────────────────────────────────────
    # Consultar tareas por sección
    # ──────────────────────────────────────────────

    def listar_tareas_seccion(self, nombre_seccion_corto: str) -> list[dict]:
        """
        Devuelve tareas no completadas de una sección dada ("Hoy", "Semana").

        Retorna una lista de dicts:
            {
                "emoji_prioridad": str,
                "proyecto": str,
                "name": str,
            }
        """
        seccion_gid = self._resolver_seccion_gid_por_nombre_corto(nombre_seccion_corto)
        if not seccion_gid:
            return []

        tareas = []
        opts = {
            "opt_fields": (
                "name,completed,notes,"
                "custom_fields,custom_fields.name,"
                "custom_fields.enum_value,custom_fields.enum_value.name"
            ),
        }

        for task in self.tasks_api.get_tasks_for_section(seccion_gid, opts):
            if task.get("completed"):
                continue

            nombre = task.get("name") or "(sin título)"
            notas = task.get("notes") or ""

            # Proyecto desde custom field "Proyecto"
            proyecto = "Sin proyecto"
            for cf in task.get("custom_fields", []) or []:
                if cf.get("name") == "Proyecto":
                    enum_val = cf.get("enum_value")
                    raw = (enum_val or {}).get("name")
                    if raw:
                        if " " in raw and not raw[0].isalnum():
                            proyecto = raw.split(" ", 1)[1]
                        else:
                            proyecto = raw
                    break

            # Fallback: intentar parsear desde las notas
            if proyecto == "Sin proyecto":
                for line in notas.splitlines():
                    if line.startswith("Proyecto:"):
                        proyecto = line.split("Proyecto:", 1)[1].strip()
                        break

            # Emoji de prioridad desde notas (línea "Prioridad: {emoji} ...")
            emoji_prioridad = "•"
            for line in notas.splitlines():
                if line.startswith("Prioridad:"):
                    rest = line.split("Prioridad:", 1)[1].strip()
                    if rest:
                        emoji_prioridad = rest.split()[0]
                    break

            tareas.append(
                {
                    "gid": task.get("gid"),
                    "emoji_prioridad": emoji_prioridad,
                    "proyecto": proyecto,
                    "name": nombre,
                    "seccion": nombre_seccion_corto,
                }
            )

        return tareas

    def completar_tarea(self, task_gid: str):
        """Marca una tarea como completada y la mueve a la sección 'Hecho'."""
        # Marcar como completada
        self.tasks_api.update_task(
            {"data": {"completed": True}},
            task_gid,
            {},
        )

        # Mover a sección "Hecho" si existe
        seccion_hecho_gid = self._resolver_seccion_gid_por_nombre_corto("Hecho")
        if seccion_hecho_gid:
            self.sections_api.add_task_for_section(
                seccion_hecho_gid,
                {"body": {"data": {"task": task_gid}}},
            )
            logger.info(f"✅ Tarea {task_gid} movida a sección 'Hecho'")

    # ──────────────────────────────────────────────
    # Deadlines próximos
    # ──────────────────────────────────────────────

    def obtener_deadlines(self, hoy: date | None = None) -> dict:
        """
        Devuelve tareas que vencen hoy o mañana (hora Argentina).

        Returns:
            {
                "hoy":   [{"name": str, "proyecto": str}, ...],
                "manana": [{"name": str, "proyecto": str}, ...],
            }
        """
        from zoneinfo import ZoneInfo

        if hoy is None:
            hoy = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date()

        manana = hoy + timedelta(days=1)

        hoy_list: list[dict] = []
        manana_list: list[dict] = []

        opts = {
            "opt_fields": (
                "name,completed,due_on,notes,"
                "custom_fields,custom_fields.name,"
                "custom_fields.enum_value,custom_fields.enum_value.name"
            ),
        }

        for nombre_seccion in ("Hoy", "Semana", "Backlog"):
            seccion_gid = self._resolver_seccion_gid_por_nombre_corto(nombre_seccion)
            if not seccion_gid:
                continue

            for task in self.tasks_api.get_tasks_for_section(seccion_gid, opts):
                if task.get("completed"):
                    continue

                due_on_raw = task.get("due_on")
                if not due_on_raw:
                    continue

                try:
                    due_date = date.fromisoformat(due_on_raw)
                except Exception:
                    logger.warning(f"⚠️ No se pudo parsear due_on: {due_on_raw}")
                    continue

                nombre = task.get("name") or "(sin título)"
                proyecto = self._extraer_proyecto_desde_task(task)
                entry = {"name": nombre, "proyecto": proyecto}

                if due_date == hoy:
                    hoy_list.append(entry)
                elif due_date == manana:
                    manana_list.append(entry)

        hoy_list.sort(key=lambda t: (t["proyecto"], t["name"]))
        manana_list.sort(key=lambda t: (t["proyecto"], t["name"]))

        return {"hoy": hoy_list, "manana": manana_list}

    # ──────────────────────────────────────────────
    # Resumen semanal
    # ──────────────────────────────────────────────

    def _extraer_proyecto_desde_task(self, task: dict) -> str:
        """Obtiene el nombre de proyecto normalizado desde custom fields / notas."""
        proyecto = "Sin proyecto"
        notas = task.get("notes") or ""

        # Desde custom field "Proyecto"
        for cf in task.get("custom_fields", []) or []:
            if cf.get("name") == "Proyecto":
                enum_val = cf.get("enum_value")
                raw = (enum_val or {}).get("name")
                if raw:
                    if " " in raw and not raw[0].isalnum():
                        proyecto = raw.split(" ", 1)[1]
                    else:
                        proyecto = raw
                break

        # Fallback: intentar parsear desde las notas
        if proyecto == "Sin proyecto":
            for line in notas.splitlines():
                if line.startswith("Proyecto:"):
                    proyecto = line.split("Proyecto:", 1)[1].strip()
                    break

        return proyecto

    def obtener_resumen_semanal(self, hoy: date | None = None) -> dict:
        """
        Devuelve un resumen semanal:

        - Tareas completadas en la sección "Hecho" en los últimos 7 días (completed_at)
        - Tareas vencidas/no completadas en secciones Hoy, Semana, Backlog (due_on < hoy)
        - Conteo de completadas por proyecto (custom field "Proyecto")
        """
        if hoy is None:
            hoy = datetime.now(timezone.utc).date()

        desde = hoy - timedelta(days=6)  # ventana de 7 días: hoy y 6 días hacia atrás

        completadas: list[dict] = []
        vencidas: list[dict] = []
        por_proyecto: dict[str, int] = {}

        # ── Completadas en "Hecho" ─────────────────────────────────────────
        seccion_hecho_gid = self._resolver_seccion_gid_por_nombre_corto("Hecho")
        if seccion_hecho_gid:
            opts_hecho = {
                "opt_fields": (
                    "name,completed,completed_at,notes,"
                    "custom_fields,custom_fields.name,"
                    "custom_fields.enum_value,custom_fields.enum_value.name"
                ),
            }
            for task in self.tasks_api.get_tasks_for_section(seccion_hecho_gid, opts_hecho):
                if not task.get("completed"):
                    continue

                completed_at_raw = task.get("completed_at")
                if not completed_at_raw:
                    continue

                try:
                    # Asana devuelve ISO 8601, normalmente con 'Z'
                    completed_dt = datetime.fromisoformat(
                        completed_at_raw.replace("Z", "+00:00")
                    )
                    completed_date = completed_dt.date()
                except Exception:
                    logger.warning(f"⚠️ No se pudo parsear completed_at: {completed_at_raw}")
                    continue

                if not (desde <= completed_date <= hoy):
                    continue

                proyecto = self._extraer_proyecto_desde_task(task)
                nombre = task.get("name") or "(sin título)"

                completadas.append(
                    {
                        "name": nombre,
                        "proyecto": proyecto,
                    }
                )

                por_proyecto[proyecto] = por_proyecto.get(proyecto, 0) + 1

        # ── Vencidas en Hoy / Semana / Backlog ─────────────────────────────
        opts_pendientes = {
            "opt_fields": (
                "name,completed,due_on,notes,"
                "custom_fields,custom_fields.name,"
                "custom_fields.enum_value,custom_fields.enum_value.name"
            ),
        }

        for nombre_seccion in ("Hoy", "Semana", "Backlog"):
            seccion_gid = self._resolver_seccion_gid_por_nombre_corto(nombre_seccion)
            if not seccion_gid:
                continue

            for task in self.tasks_api.get_tasks_for_section(seccion_gid, opts_pendientes):
                if task.get("completed"):
                    continue

                due_on_raw = task.get("due_on")
                if not due_on_raw:
                    continue

                try:
                    due_date = date.fromisoformat(due_on_raw)
                except Exception:
                    logger.warning(f"⚠️ No se pudo parsear due_on: {due_on_raw}")
                    continue

                if due_date >= hoy:
                    continue

                nombre = task.get("name") or "(sin título)"
                proyecto = self._extraer_proyecto_desde_task(task)

                vencidas.append(
                    {
                        "name": nombre,
                        "proyecto": proyecto,
                        "due_on": due_date,
                    }
                )

        # Orden simple: por nombre de proyecto luego nombre de tarea
        completadas.sort(key=lambda t: (t["proyecto"], t["name"]))
        vencidas.sort(key=lambda t: (t["proyecto"], t["due_on"], t["name"]))

        return {
            "desde": desde,
            "hasta": hoy,
            "completadas": completadas,
            "vencidas": vencidas,
            "por_proyecto": por_proyecto,
        }
