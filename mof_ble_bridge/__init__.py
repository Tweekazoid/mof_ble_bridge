"""Ministry of Flat Bridge — Blender addon for UV unwrapping via Ministry of Flat.

Blender bridge by Michal Hons (mehpixel.com).
Ministry of Flat by Eskil Steenberg (quelsolaar.com).
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from urllib import request

import bpy
from bpy.props import (
    BoolProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Context, Operator, Panel
from bpy.utils import previews

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
try:
    _version_file = Path(__file__).parent / "__version__"
    _VERSION_STR = _version_file.read_text(encoding="utf-8").strip()
    _VERSION = tuple(int(x) for x in _VERSION_STR.split("."))
except Exception:  # noqa: BLE001
    _VERSION_STR = "0.1.0"
    _VERSION = (0, 1, 0)

bl_info = {
    "name": "Ministry of Flat Bridge",
    "author": "Michal Hons (mehpixel.com) | MoF by Eskil Steenberg (quelsolaar.com)",
    "version": (0, 1, 0),
    "blender": (3, 0, 0),
    "description": (
        "UV unwrap bridge for Ministry of Flat automatic unwrapper "
        "by Eskil Steenberg — quelsolaar.com"
    ),
    "warning": "Requires Ministry of Flat binaries (auto-download available in Preferences)",
    "doc_url": "https://www.quelsolaar.com",
    "category": "UV",
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MOF_DOWNLOAD_URL = "https://www.quelsolaar.com/MinistryOfFlat_Release.zip"
_ADDON_DIR = Path(__file__).parent
_DEFAULT_MOF_DIR = str(_ADDON_DIR / "MinistryOfFlat_Release")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_preview_collections: dict = {}
_download_state: dict = {"running": False, "status": ""}


# ---------------------------------------------------------------------------
# Icon helpers
# ---------------------------------------------------------------------------
def _get_icon_id(name: str = "mof_ble") -> int:
    """Return the custom icon id, or 0 if unavailable."""
    pcoll = _preview_collections.get("main")
    if pcoll and name in pcoll:
        return pcoll[name].icon_id
    return 0


def _load_icons() -> None:
    """Load custom icons from the icons sub-folder."""
    pcoll = previews.new()
    icon_file = _ADDON_DIR / "icons" / "mof_ble.png"
    if icon_file.exists():
        pcoll.load("mof_ble", str(icon_file), "IMAGE")
    _preview_collections["main"] = pcoll


def _unload_icons() -> None:
    """Release custom icon resources."""
    for pcoll in _preview_collections.values():
        previews.remove(pcoll)
    _preview_collections.clear()


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------
def _get_mof_exe(context: Context) -> str:
    """Return the resolved path to UnWrapConsole3.exe."""
    prefs = context.preferences.addons[__name__].preferences
    folder = prefs.mof_folder_path or _DEFAULT_MOF_DIR
    return os.path.join(folder, "UnWrapConsole3.exe")


def _mof_exe_exists(context: Context) -> bool:
    """Check whether the Ministry of Flat executable is present."""
    return os.path.isfile(_get_mof_exe(context))


def _bool_flag(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _build_cmd(exe: str, input_path: str, output_path: str, props: MOF_OT_unwrap) -> list[str]:
    """Build the full command argument list from operator properties.

    Flag names and order match the Ministry of Flat 3.7.2 documentation.
    """
    cx, cy, cz = props.seam_center
    cmd = [exe, input_path, output_path]
    # Normal settings (docs: Texture resolution … Seam direction)
    cmd += ["-RESOLUTION", str(props.resolution)]
    cmd += ["-SEPARATE", _bool_flag(props.separate)]
    cmd += ["-ASPECT", f"{props.aspect:.6f}"]
    cmd += ["-NORMALS", _bool_flag(props.use_normals)]
    cmd += ["-UDIMS", str(props.udims)]
    cmd += ["-OVERLAP", _bool_flag(props.overlap)]
    cmd += ["-MIRROR", _bool_flag(props.mirror)]
    cmd += ["-WORLDSCALE", _bool_flag(props.worldscale)]
    cmd += ["-DENSITY", f"{props.density:.0f}"]
    cmd += ["-CENTER", f"{cx:.6f}", f"{cy:.6f}", f"{cz:.6f}"]
    # Debug-only settings (docs: Supress … Validate)
    cmd += ["-SUPRESS", _bool_flag(props.suppress_geo_errors)]
    cmd += ["-QUAD", _bool_flag(props.quad)]
    cmd += ["-WELD", "FALSE"]  # intentionally off — MoF's weld is internal analysis only
    cmd += ["-FLAT", _bool_flag(props.flat_surface)]
    cmd += ["-CONE", _bool_flag(props.cone)]
    cmd += ["-CONERATIO", f"{props.coneratio:.6f}"]
    cmd += ["-GRIDS", _bool_flag(props.grids)]
    cmd += ["-STRIP", _bool_flag(props.strip)]
    cmd += ["-PATCH", _bool_flag(props.patch)]
    cmd += ["-PLANES", _bool_flag(props.planes)]
    cmd += ["-FLATT", f"{props.flatness:.6f}"]
    cmd += ["-MERGE", _bool_flag(props.merge)]
    cmd += ["-MERGELIMIT", f"{props.mergelimit:.6f}"]
    cmd += ["-PRESMOOTH", _bool_flag(props.presmooth)]
    cmd += ["-SOFTUNFOLD", _bool_flag(props.softunfold)]
    cmd += ["-TUBES", _bool_flag(props.tubes)]
    cmd += ["-JUNCTIONSDEBUG", _bool_flag(props.junctions)]
    cmd += ["-EXTRADEBUG", _bool_flag(props.extra_debug)]
    cmd += ["-ABF", _bool_flag(props.abf)]
    cmd += ["-SMOOTH", _bool_flag(props.smooth_cut)]
    cmd += ["-REPAIRSMOOTH", _bool_flag(props.repairsmooth)]
    cmd += ["-REPAIR", _bool_flag(props.repair)]
    cmd += ["-SQUARE", _bool_flag(props.square)]
    cmd += ["-RELAX", _bool_flag(props.relax)]
    cmd += ["-RELAX_ITERATIONS", str(props.relax_iteration)]
    cmd += ["-EXPAND", f"{props.expand:.6f}"]
    cmd += ["-CUTDEBUG", _bool_flag(props.cut_debug)]
    cmd += ["-STRETCH", _bool_flag(props.stretch)]
    cmd += ["-MATCH", _bool_flag(props.match)]
    cmd += ["-PACKING", _bool_flag(props.packing)]
    cmd += ["-RASTERIZATION", str(props.rasterization)]
    cmd += ["-PACKING_ITERATIONS", str(props.packing_iteration)]
    cmd += ["-SCALETOFIT", f"{props.scaletofit:.6f}"]
    cmd += ["-VALIDATE", _bool_flag(props.validate)]
    cmd += ["-SILENT", _bool_flag(props.silent)]
    return cmd


# ---------------------------------------------------------------------------
# Download operator
# ---------------------------------------------------------------------------
class MOF_OT_download(Operator):  # noqa: N801
    """Download and install Ministry of Flat binaries from quelsolaar.com."""

    bl_idname = "mof_ble.download_mof"
    bl_label = "Download Ministry of Flat"
    bl_description = (
        "Download the free Ministry of Flat binaries from quelsolaar.com "
        "and extract them into the addon folder"
    )
    bl_options = {"REGISTER"}

    def execute(self, context: Context) -> set[str]:
        """Start the background download thread."""
        if _download_state["running"]:
            self.report({"WARNING"}, "Download already in progress")
            return {"CANCELLED"}

        prefs = context.preferences.addons[__name__].preferences
        target_dir = prefs.mof_folder_path or _DEFAULT_MOF_DIR

        _download_state["running"] = True
        _download_state["status"] = "Starting download…"

        def _run() -> None:
            zip_path = os.path.join(bpy.app.tempdir, "MinistryOfFlat_Release.zip")
            try:
                LOG.info("Downloading Ministry of Flat from %s", MOF_DOWNLOAD_URL)
                _download_state["status"] = "Downloading…"
                request.urlretrieve(MOF_DOWNLOAD_URL, zip_path)  # noqa: S310

                LOG.info("Extracting to %s", target_dir)
                _download_state["status"] = "Extracting…"
                os.makedirs(target_dir, exist_ok=True)

                with zipfile.ZipFile(zip_path, "r") as zf:
                    for member in zf.namelist():
                        # Zip-slip protection: resolve and verify destination
                        parts = Path(member).parts
                        # Strip the top-level folder from the zip (e.g. MinistryOfFlat_Release/)
                        rel_parts = parts[1:] if len(parts) > 1 else parts
                        rel = os.path.join(*rel_parts) if rel_parts else ""
                        if not rel:
                            continue
                        dest = os.path.realpath(os.path.join(target_dir, rel))
                        real_target = os.path.realpath(target_dir)
                        # Validate no path traversal
                        if not dest.startswith(real_target + os.sep) and dest != real_target:
                            LOG.warning("Skipping unsafe zip entry: %s", member)
                            continue
                        if member.endswith("/"):
                            os.makedirs(dest, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            with zf.open(member) as src, open(dest, "wb") as out:
                                out.write(src.read())

                _download_state["status"] = "Done! Restart or reload addon."
                LOG.info("Ministry of Flat installed to %s", target_dir)
            except Exception as exc:  # broad catch needed — unknown download/extraction errors
                LOG.exception("Download failed")
                _download_state["status"] = f"Error: {exc}"
            finally:
                _download_state["running"] = False
                with contextlib.suppress(OSError):
                    os.remove(zip_path)

        threading.Thread(target=_run, daemon=True).start()
        self.report({"INFO"}, f"Downloading Ministry of Flat into: {target_dir}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Addon Preferences
# ---------------------------------------------------------------------------
class MOFBridgePreferences(AddonPreferences):
    """Addon preferences for Ministry of Flat Bridge."""

    bl_idname = __name__

    mof_folder_path: StringProperty(  # type: ignore[valid-type]
        name="Ministry of Flat Folder",
        description="Folder containing UnWrapConsole3.exe",
        subtype="DIR_PATH",
        default=_DEFAULT_MOF_DIR,
    )

    def draw(self, context: Context) -> None:  # noqa: ARG002
        """Draw the preferences panel."""
        layout = self.layout
        icon_id = _get_icon_id()

        row = layout.row()
        row.label(
            text="Ministry of Flat Bridge",
            icon_value=icon_id if icon_id else 0,
            icon="UV" if not icon_id else "NONE",
        )

        # Executable location
        box = layout.box()
        box.label(text="Executable Location", icon="FILE_FOLDER")
        box.prop(self, "mof_folder_path")

        exe = os.path.join(self.mof_folder_path or _DEFAULT_MOF_DIR, "UnWrapConsole3.exe")
        if os.path.isfile(exe):
            box.label(text="✔  UnWrapConsole3.exe found", icon="CHECKMARK")
        else:
            box.label(text="UnWrapConsole3.exe not found", icon="ERROR")
            row2 = box.row()
            row2.scale_y = 1.4
            row2.operator(
                "mof_ble.download_mof",
                icon="IMPORT",
                text="Download Ministry of Flat  (quelsolaar.com)",
            )
            if _download_state["running"]:
                box.label(text=_download_state["status"], icon="TIME")
            elif _download_state["status"]:
                box.label(text=_download_state["status"])

        # Credits
        box2 = layout.box()
        box2.label(text="Credits", icon="INFO")
        box2.label(text="Ministry of Flat  —  Eskil Steenberg   quelsolaar.com")
        box2.label(text="Blender Bridge  —  Michal Hons   mehpixel.com")


# ---------------------------------------------------------------------------
# Main Unwrap Operator
# ---------------------------------------------------------------------------
class MOF_OT_unwrap(Operator):  # noqa: N801
    """Unwrap selected mesh objects using Ministry of Flat."""

    bl_idname = "mof_ble.unwrap"
    bl_label = "MoF Auto Unwrap"
    bl_description = "Automatically unwrap UVs on selected mesh objects using Ministry of Flat"
    bl_options = {"REGISTER", "UNDO"}

    # -- Basic -------------------------------------------------------------- #
    resolution: IntProperty(  # type: ignore[valid-type]
        name="Texture Resolution",
        description="Texture resolution — controls island gap size to prevent bleeds",
        default=1024,
        min=64,
        max=16384,
    )
    aspect: FloatProperty(  # type: ignore[valid-type]
        name="Pixel Aspect",
        description="Aspect ratio of pixels for non-square textures",
        default=1.0,
        min=0.01,
        max=100.0,
    )
    udims: IntProperty(  # type: ignore[valid-type]
        name="UDIMs",
        description="Split the model into multiple UDIMs",
        default=1,
        min=1,
        max=32,
    )
    separate: BoolProperty(  # type: ignore[valid-type]
        name="Separate Hard Edges",
        description="Guarantee all hard edges are separated — useful for lightmapping / normal maps",
        default=False,
    )
    use_normals: BoolProperty(  # type: ignore[valid-type]
        name="Use Normals",
        description="Use mesh normals to help classify polygons",
        default=False,
    )

    # -- Overlap ------------------------------------------------------------ #
    overlap: BoolProperty(  # type: ignore[valid-type]
        name="Overlap Identical Parts",
        description="Overlap identical parts to save texture space",
        default=False,
    )
    mirror: BoolProperty(  # type: ignore[valid-type]
        name="Overlap Mirrored Parts",
        description="Overlap mirrored parts to save texture space",
        default=False,
    )

    # -- World Scale -------------------------------------------------------- #
    worldscale: BoolProperty(  # type: ignore[valid-type]
        name="World Scale UVs",
        description="Scale UVs to match real-world scale (beyond 0-1 range)",
        default=False,
    )
    density: FloatProperty(  # type: ignore[valid-type]
        name="Texture Density (px/unit)",
        description="Pixels per unit when World Scale is enabled",
        default=1024.0,
        min=1.0,
        max=65536.0,
    )

    # -- Seam direction ----------------------------------------------------- #
    seam_center: FloatVectorProperty(  # type: ignore[valid-type]
        name="Seam Direction Center",
        description="Point in space that seams are directed towards (default: model center)",
        default=(0.0, 0.0, 0.0),
        subtype="XYZ",
    )

    # -- Geometry analysis -------------------------------------------------- #
    quad: BoolProperty(  # type: ignore[valid-type]
        name="Find Quads",
        description="Search for triangle pairs that form good quads",
        default=True,
    )
    flat_surface: BoolProperty(  # type: ignore[valid-type]
        name="Flat Soft Surface",
        description="Detect flat areas of soft surfaces to minimise distortion",
        default=True,
    )
    cone: BoolProperty(  # type: ignore[valid-type]
        name="Detect Cones",
        description="Search for sharp cone geometry",
        default=True,
    )
    coneratio: FloatProperty(  # type: ignore[valid-type]
        name="Cone Ratio",
        description="Minimum ratio of a triangle used in a cone",
        default=0.5,
        min=0.0,
        max=1.0,
    )
    grids: BoolProperty(  # type: ignore[valid-type]
        name="Detect Grids",
        description="Search for grids of quads",
        default=True,
    )
    strip: BoolProperty(  # type: ignore[valid-type]
        name="Detect Strips",
        description="Search for strips of quads",
        default=True,
    )
    patch: BoolProperty(  # type: ignore[valid-type]
        name="Detect Patches",
        description="Search for patches of quads",
        default=True,
    )
    planes: BoolProperty(  # type: ignore[valid-type]
        name="Detect Planes",
        description="Detect flat planes in the mesh",
        default=True,
    )
    flatness: FloatProperty(  # type: ignore[valid-type]
        name="Flatness Threshold",
        description="Minimum normal dot product between two flat polygons",
        default=0.9,
        min=0.0,
        max=1.0,
    )

    # -- Unfolding ---------------------------------------------------------- #
    merge: BoolProperty(  # type: ignore[valid-type]
        name="Merge Islands",
        description="Merge polygons using unfolding",
        default=True,
    )
    mergelimit: FloatProperty(  # type: ignore[valid-type]
        name="Merge Angle Limit",
        description="Limit the angle of polygons being merged",
        default=0.0,
        min=0.0,
        max=180.0,
    )
    presmooth: BoolProperty(  # type: ignore[valid-type]
        name="Pre-Smooth",
        description="Soften the mesh before attempting to cut and project",
        default=True,
    )
    softunfold: BoolProperty(  # type: ignore[valid-type]
        name="Soft Unfold",
        description="Attempt to unfold soft surfaces",
        default=True,
    )
    tubes: BoolProperty(  # type: ignore[valid-type]
        name="Detect Tubes",
        description="Find tube-shaped geometry and unwrap it using cylindrical projection",
        default=True,
    )
    junctions: BoolProperty(  # type: ignore[valid-type]
        name="Tube Junctions",
        description="Find and handle junctions between tubes (-JUNCTIONSDEBUG)",
        default=True,
    )
    extra_debug: BoolProperty(  # type: ignore[valid-type]
        name="Extra Ordinary Points",
        description="Use vertices not shared by 4 quads as starting points for cutting (-EXTRADEBUG)",
        default=False,
    )
    abf: BoolProperty(  # type: ignore[valid-type]
        name="Angle Based Flattening",
        description="Use angle-based flattening (ABF) to handle smooth surfaces",
        default=True,
    )
    smooth_cut: BoolProperty(  # type: ignore[valid-type]
        name="Smooth Cut & Project",
        description="Cut and project smooth surfaces",
        default=True,
    )
    repairsmooth: BoolProperty(  # type: ignore[valid-type]
        name="Repair Smooth Islands",
        description="Attach small islands to larger islands on smooth surfaces",
        default=True,
    )

    # -- Post-processing ---------------------------------------------------- #
    repair: BoolProperty(  # type: ignore[valid-type]
        name="Repair UV Edges",
        description="Repair edges to make them straight",
        default=True,
    )
    square: BoolProperty(  # type: ignore[valid-type]
        name="Square Polygons",
        description="Find individual polygons with right angles",
        default=True,
    )
    relax: BoolProperty(  # type: ignore[valid-type]
        name="Relax UVs",
        description="Relax all smooth polygons to minimise distortion",
        default=True,
    )
    relax_iteration: IntProperty(  # type: ignore[valid-type]
        name="Relax Iterations",
        description="Number of iteration loops during relaxation",
        default=50,
        min=1,
        max=500,
    )
    expand: FloatProperty(  # type: ignore[valid-type]
        name="Expand Soft Islands",
        description="Expand soft surfaces to make more use of texture space. Experimental, off by default (-EXPAND)",
        default=0.25,
        min=0.0,
        max=1.0,
    )
    cut_debug: BoolProperty(  # type: ignore[valid-type]
        name="Cut Awkward Shapes",
        description="Cut down awkward shapes in order to optimise layout coverage (-CUTDEBUG)",
        default=True,
    )
    stretch: BoolProperty(  # type: ignore[valid-type]
        name="Stretch to Fit",
        description="Stretch any island that is too wide to fit in the image",
        default=True,
    )
    match: BoolProperty(  # type: ignore[valid-type]
        name="Match Triangles",
        description="Match individual triangles for better packing coverage",
        default=True,
    )

    # -- Packing ------------------------------------------------------------ #
    packing: BoolProperty(  # type: ignore[valid-type]
        name="Pack Islands",
        description="Pack islands into a rectangle",
        default=True,
    )
    rasterization: IntProperty(  # type: ignore[valid-type]
        name="Pack Rasterization",
        description="Resolution of packing rasterization grid",
        default=64,
        min=8,
        max=1024,
    )
    packing_iteration: IntProperty(  # type: ignore[valid-type]
        name="Pack Iterations",
        description="How many times the packer runs to find optimal island spacing",
        default=4,
        min=1,
        max=32,
    )
    scaletofit: FloatProperty(  # type: ignore[valid-type]
        name="Scale to Fit Cavities",
        description="Scale islands to fit cavities",
        default=0.5,
        min=0.0,
        max=1.0,
    )

    # -- Debug / misc ------------------------------------------------------- #
    silent: BoolProperty(  # type: ignore[valid-type]
        name="Silent",
        description="Suppress console output from Ministry of Flat",
        default=True,
    )
    suppress_geo_errors: BoolProperty(  # type: ignore[valid-type]
        name="Suppress Geometry Errors",
        description="Faulty geometry errors will not be printed to stdout",
        default=False,
    )
    validate: BoolProperty(  # type: ignore[valid-type]
        name="Validate Geometry",
        description="Validate geometry after each stage and print any issues (debug only)",
        default=False,
    )

    # -- UI toggle ---------------------------------------------------------- #
    show_advanced: BoolProperty(  # type: ignore[valid-type]
        name="Show Advanced / Debug Settings",
        description="Show geometry analysis, unfolding, post-processing and packing controls",
        default=False,
    )

    # -- Operator UI -------------------------------------------------------- #
    def invoke(self, context: Context, event) -> set[str]:  # noqa: ARG002
        """Show the settings dialog before running."""
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context: Context) -> None:  # noqa: ARG002, PLR0915
        """Draw operator properties grouped by category."""
        layout = self.layout

        # Basic
        box = layout.box()
        box.label(text="Basic", icon="UV")
        col = box.column(align=True)
        col.prop(self, "resolution")
        col.prop(self, "aspect")
        col.prop(self, "udims")
        col.separator()
        col.prop(self, "separate")
        col.prop(self, "use_normals")

        # Overlap
        box = layout.box()
        box.label(text="Overlap", icon="OVERLAY")
        col = box.column(align=True)
        col.prop(self, "overlap")
        col.prop(self, "mirror")

        # World Scale
        box = layout.box()
        box.label(text="World Scale", icon="WORLD")
        col = box.column(align=True)
        col.prop(self, "worldscale")
        sub = col.column(align=True)
        sub.enabled = self.worldscale
        sub.prop(self, "density")

        # Seam Direction  (last normal/user setting per MoF docs)
        box = layout.box()
        box.label(text="Seam Direction", icon="DRIVER_ROTATIONAL_DIFFERENCE")
        box.prop(self, "seam_center", text="")

        # ------------------------------------------------------------------ #
        # Advanced / Debug toggle  (docs: changing these likely worsens results)
        # ------------------------------------------------------------------ #
        layout.separator()
        adv_header = layout.row()
        adv_header.prop(
            self,
            "show_advanced",
            icon="TRIA_DOWN" if self.show_advanced else "TRIA_RIGHT",
            emboss=False,
        )

        if self.show_advanced:
            warn = layout.box()
            warn.alert = True
            warn.label(text="Advanced / Debug Settings", icon="ERROR")
            warn.label(text="Changing these may worsen results or slow processing")

            # Geometry Analysis
            box = layout.box()
            box.label(text="Geometry Analysis", icon="MESH_DATA")
            col = box.column(align=True)
            col.prop(self, "quad")
            col.prop(self, "flat_surface")
            col.prop(self, "cone")
            sub = col.column(align=True)
            sub.enabled = self.cone
            sub.prop(self, "coneratio")
            col.prop(self, "grids")
            col.prop(self, "strip")
            col.prop(self, "patch")
            col.prop(self, "planes")
            col.prop(self, "flatness")

            # Unfolding
            box = layout.box()
            box.label(text="Unfolding", icon="MOD_UVPROJECT")
            col = box.column(align=True)
            col.prop(self, "merge")
            sub = col.column(align=True)
            sub.enabled = self.merge
            sub.prop(self, "mergelimit")
            col.prop(self, "presmooth")
            col.prop(self, "softunfold")
            col.prop(self, "tubes")
            sub = col.column(align=True)
            sub.enabled = self.tubes
            sub.prop(self, "junctions")
            col.prop(self, "extra_debug")
            col.prop(self, "abf")
            col.prop(self, "smooth_cut")
            col.prop(self, "repairsmooth")

            # Post-Processing
            box = layout.box()
            box.label(text="Post-Processing", icon="MODIFIER")
            col = box.column(align=True)
            col.prop(self, "repair")
            col.prop(self, "square")
            col.prop(self, "relax")
            sub = col.column(align=True)
            sub.enabled = self.relax
            sub.prop(self, "relax_iteration")
            col.prop(self, "expand")
            col.prop(self, "cut_debug")
            col.prop(self, "stretch")
            col.prop(self, "match")

            # Packing
            box = layout.box()
            box.label(text="Packing", icon="PACKAGE")
            col = box.column(align=True)
            col.prop(self, "packing")
            sub = col.column(align=True)
            sub.enabled = self.packing
            sub.prop(self, "rasterization")
            sub.prop(self, "packing_iteration")
            sub.prop(self, "scaletofit")

            # Misc / Silent
            box = layout.box()
            box.label(text="Output", icon="TOOL_SETTINGS")
            col = box.column(align=True)
            col.prop(self, "silent")
            col.prop(self, "suppress_geo_errors")
            col.prop(self, "validate")

    # -- Execution ---------------------------------------------------------- #
    def execute(self, context: Context) -> set[str]:
        """Run Ministry of Flat on all selected mesh objects."""
        if not _mof_exe_exists(context):
            self.report(
                {"ERROR"},
                "Ministry of Flat executable not found. "
                "Set the path in Addon Preferences or use the Download button.",
            )
            return {"CANCELLED"}

        exe = _get_mof_exe(context)
        selected = [o for o in context.selected_objects if o.type == "MESH"]
        if not selected:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        original_active = context.view_layer.objects.active
        processed = 0
        errors = 0

        for obj in selected:
            try:
                self._process_object(context, obj, exe)
                processed += 1
            except Exception as exc:  # broad catch — unknown per-object errors
                LOG.exception("Failed to process %s", obj.name)
                self.report({"WARNING"}, f"Failed on '{obj.name}': {exc}")
                errors += 1

        context.view_layer.objects.active = original_active

        if errors:
            self.report({"WARNING"}, f"Unwrapped {processed} object(s), {errors} error(s). Check console.")
        else:
            self.report({"INFO"}, f"Ministry of Flat: unwrapped {processed} object(s) successfully.")
        return {"FINISHED"}

    def _process_object(self, context: Context, obj, exe: str) -> None:
        """Export → MoF → import → transfer UVs back for a single object."""
        tmp = bpy.app.tempdir
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in obj.name)
        fn_in = os.path.join(tmp, f"{safe_name}_mof_in.obj")
        fn_out = os.path.join(tmp, f"{safe_name}_mof_out.obj")

        # -- Export this object only --
        # Use Y-forward / Z-up axes (standard OBJ convention).
        # Blender 4.x wm.obj_export defaults differ from this, causing MoF
        # to receive geometry in a coordinate system it cannot read correctly.
        #
        # Export via a temporary duplicate whose world matrix is reset to identity
        # so unapplied scale/rotation on the original does not get baked into
        # the OBJ vertex positions and distort MoF's UV analysis.
        import mathutils  # bpy-only module, unavailable outside Blender
        tmp_obj = obj.copy()
        tmp_obj.data = obj.data.copy()
        tmp_obj.matrix_world = mathutils.Matrix.Identity(4)
        context.collection.objects.link(tmp_obj)

        bpy.ops.object.select_all(action="DESELECT")
        tmp_obj.select_set(True)
        context.view_layer.objects.active = tmp_obj
        bpy.ops.wm.obj_export(
            filepath=fn_in,
            export_selected_objects=True,
            export_materials=False,
            forward_axis="Y",
            up_axis="Z",
        )
        bpy.data.objects.remove(tmp_obj, do_unlink=True)

        # Defensive: ensure the file was fully written before handing it to MoF.
        if not os.path.isfile(fn_in) or os.path.getsize(fn_in) == 0:
            raise RuntimeError(f"OBJ export produced no output (or empty file) at: {fn_in}")

        # -- Run Ministry of Flat --
        cmd = _build_cmd(exe, fn_in, fn_out, self)
        LOG.info("Running MoF: %s", " ".join(cmd))

        kwargs: dict = {"capture_output": True, "text": True, "timeout": 600}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        result = subprocess.run(cmd, check=False, **kwargs)

        # MoF exits with code 1 even on success — rely on output file presence instead.
        if not os.path.isfile(fn_out) or os.path.getsize(fn_out) == 0:
            raise RuntimeError(
                f"UnWrapConsole3 exited with code {result.returncode} and produced no output:\n"
                f"{result.stderr or result.stdout or '(no output)'}",
            )

        # -- Import MoF result using the same axis convention --
        before = set(bpy.data.objects.keys())
        bpy.ops.wm.obj_import(filepath=fn_out, forward_axis="Y", up_axis="Z")
        after = set(bpy.data.objects.keys())
        imported_names = after - before

        if not imported_names:
            raise RuntimeError("OBJ import produced no new object.")

        imported_obj = bpy.data.objects[next(iter(imported_names))]

        # -- Transfer UVs using Blender's DataTransfer modifier (TOPOLOGY mapping) --
        # More robust than raw loop-index copy; handles any vertex-order differences.
        try:
            _transfer_uvs_data_transfer(context, imported_obj, obj)
        except Exception as exc:  # noqa: BLE001 — UV transfer errors are unknown/varied
            LOG.warning("UV transfer failed (%s), keeping imported object.", exc)
            self.report(
                {"WARNING"},
                f"'{obj.name}': UV transfer failed ({exc}). "
                "Imported result kept as a separate object.",
            )
            return

        # Remove the temporary imported object
        bpy.ops.object.select_all(action="DESELECT")
        imported_obj.select_set(True)
        context.view_layer.objects.active = imported_obj
        bpy.ops.object.delete()

        # -- Cleanup temp files --
        for fp in (fn_in, fn_out):
            with contextlib.suppress(OSError):
                os.remove(fp)


def _transfer_uvs_data_transfer(context: Context, source: object, target: object) -> None:
    """Transfer UVs from source (MoF result) to target using DataTransfer modifier.

    Uses TOPOLOGY mapping -- same face/vertex order is preserved by MoF.
    This is more robust than raw loop-index copy as it goes through
    Blender's own UV transfer pipeline.

    Args:
        context: Blender context.
        source: Blender Object with MoF-processed UV data.
        target: Blender Object whose UV map will be updated.

    Raises:
        ValueError: If the imported mesh has no UV layers.
    """
    if not source.data.uv_layers:
        raise ValueError("Imported mesh has no UV layers.")

    # Rename the imported UV layer to match the target's active UV map name
    tgt_uv_name = target.data.uv_layers.active.name if target.data.uv_layers else "UVMap"
    source.data.uv_layers[0].name = tgt_uv_name

    # Ensure target has a UV layer to receive into
    if not target.data.uv_layers:
        target.data.uv_layers.new(name=tgt_uv_name)

    # Apply DataTransfer modifier on the target object
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    context.view_layer.objects.active = target

    dt_mod = target.modifiers.new(name="_mof_uv_transfer", type="DATA_TRANSFER")
    dt_mod.object = source
    dt_mod.use_loop_data = True
    dt_mod.data_types_loops = {"UV"}
    dt_mod.loop_mapping = "TOPOLOGY"
    dt_mod.layers_uv_select_src = "ALL"
    dt_mod.layers_uv_select_dst = "NAME"

    bpy.ops.object.modifier_apply(modifier=dt_mod.name)

    # Normalize UVs to [0, 1].
    # MoF outputs UV coordinates in an internal unit space (typically 0-3+ for a
    # cube cross layout), not normalized 0-1. Rescale min->0, max->1 uniformly so
    # the layout fits the UV grid while preserving island proportions.
    _normalize_uvs(target)


def _normalize_uvs(obj: object, margin: float = 0.002) -> None:
    """Rescale all UV coords on obj's active UV layer to fit within [margin, 1-margin].

    MoF does not guarantee output in 0-1 UV space; this step corrects that.

    Args:
        obj: Blender Object whose active UV layer will be normalized.
        margin: Border gap as a fraction of UV space (default ≈ 2 px at 1024 px).
    """
    uv_layer = obj.data.uv_layers.active
    if not uv_layer:
        return
    coords = uv_layer.data
    if not coords:
        return

    all_u = [c.uv.x for c in coords]
    all_v = [c.uv.y for c in coords]
    min_u, max_u = min(all_u), max(all_u)
    min_v, max_v = min(all_v), max(all_v)
    range_u = max_u - min_u
    range_v = max_v - min_v

    if range_u <= 0 or range_v <= 0:
        return

    scale = 1.0 - 2.0 * margin
    for c in coords:
        c.uv.x = margin + ((c.uv.x - min_u) / range_u) * scale
        c.uv.y = margin + ((c.uv.y - min_v) / range_v) * scale


# ---------------------------------------------------------------------------
# N-Panel (3D Viewport)
# ---------------------------------------------------------------------------
class MOF_PT_panel(Panel):  # noqa: N801
    """Ministry of Flat Bridge panel in the 3D Viewport N-panel."""

    bl_label = "Ministry of Flat"
    bl_idname = "MOF_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MoF Bridge"

    def draw(self, context: Context) -> None:
        """Draw the N-panel UI."""
        layout = self.layout
        icon_id = _get_icon_id()

        row = layout.row()
        if icon_id:
            row.label(text="MoF UV Unwrap", icon_value=icon_id)
        else:
            row.label(text="MoF UV Unwrap", icon="UV")

        exe_found = _mof_exe_exists(context)

        if not exe_found:
            box = layout.box()
            box.alert = True
            box.label(text="Executable not found!", icon="ERROR")
            row2 = box.row()
            row2.scale_y = 1.3
            row2.operator("mof_ble.download_mof", icon="IMPORT", text="Download Ministry of Flat")
            box.label(text="or set path in Add-on Preferences")
            if _download_state["running"]:
                box.label(text=_download_state["status"], icon="TIME")
            elif _download_state["status"]:
                box.label(text=_download_state["status"])
        else:
            layout.label(text="Executable ready", icon="CHECKMARK")

        layout.separator()

        col = layout.column()
        col.enabled = exe_found
        col.scale_y = 1.6
        col.operator(
            "mof_ble.unwrap",
            icon="UV" if not icon_id else "NONE",
            text="Auto UV Unwrap",
        )

        layout.separator()
        layout.label(text="MoF by Eskil Steenberg", icon="URL")
        layout.label(text="quelsolaar.com")
        layout.label(text="Bridge by Michal Hons")
        layout.label(text="mehpixel.com")


# ---------------------------------------------------------------------------
# UV Editor side panel
# ---------------------------------------------------------------------------
class MOF_PT_uv_panel(Panel):  # noqa: N801
    """Ministry of Flat Bridge panel in the UV editor."""

    bl_label = "Ministry of Flat"
    bl_idname = "MOF_PT_uv_panel"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "MoF Bridge"

    def draw(self, context: Context) -> None:
        """Draw the UV editor panel."""
        layout = self.layout
        exe_found = _mof_exe_exists(context)
        if not exe_found:
            layout.label(text="Executable not found", icon="ERROR")
            layout.operator("mof_ble.download_mof", icon="IMPORT", text="Download MoF")
        col = layout.column()
        col.enabled = exe_found
        col.scale_y = 1.4
        col.operator("mof_ble.unwrap", icon="UV", text="Auto UV Unwrap")


# ---------------------------------------------------------------------------
# Object menu entry
# ---------------------------------------------------------------------------
def _menu_func(self, context: Context) -> None:  # noqa: ARG001
    """Add the unwrap operator to the Object menu."""
    icon_id = _get_icon_id()
    if icon_id:
        self.layout.operator("mof_ble.unwrap", text="MoF Auto Unwrap", icon_value=icon_id)
    else:
        self.layout.operator("mof_ble.unwrap", text="MoF Auto Unwrap", icon="UV")


# ---------------------------------------------------------------------------
# Register / Unregister
# ---------------------------------------------------------------------------
_CLASSES = (
    MOFBridgePreferences,
    MOF_OT_download,
    MOF_OT_unwrap,
    MOF_PT_panel,
    MOF_PT_uv_panel,
)


def register() -> None:
    """Register all addon classes and load icons."""
    _load_icons()
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_object.append(_menu_func)


def unregister() -> None:
    """Unregister all addon classes and release icons."""
    bpy.types.VIEW3D_MT_object.remove(_menu_func)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _unload_icons()


if __name__ == "__main__":
    register()
