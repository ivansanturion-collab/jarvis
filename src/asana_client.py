"""Cliente de Asana con auto-discovery de GIDs."""

import json
import hashlib
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
        seccion_gid = self.ids["secciones"].get(nombre_seccion)

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
