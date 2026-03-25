# This file is kept for historical reference only.
# The addon entry point is now mof_ble_bridge/__init__.py
# Install the mof_ble_bridge/ folder as a Blender addon to use the full extension.
#
# See README.md for installation instructions.
import bpy
import os


bl_info = {
    "name": "Blender Mof Bridge",
    "blender": (2, 80, 0),
    "category": "UV",
}


class MyAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    folder_path: bpy.props.StringProperty(
        name="folder_path",
        description="Enter the folder path where Ministry of Flat is located",
        subtype="DIR_PATH",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "folder_path")


class AutoUV(bpy.types.Operator):
    """Automatically unwrap UVs using Ministry of Flat"""  # Tooltip shown on hover

    bl_idname = "object.autouv"  # Menu ID — must be lowercase
    bl_label = "Auto UV Unwrap"  # Name shown in the UI
    bl_options = {"REGISTER"}

    def execute(self, context):  # Logic executed on run
        # STEP 3-1: Export the selected object
        obj = context.active_object
        fn = os.path.join(bpy.app.tempdir, obj.name + ".obj")  # filename
        # Export the currently selected object
        bpy.ops.wm.obj_export(
            filepath=fn,
            export_selected_objects=True,
            export_materials=False,
        )
        # STEP 3-2: Run Ministry of Flat from the command line, passing the exported obj as argument
        fn2 = os.path.join(bpy.app.tempdir, obj.name + "_unpacked.obj")  # filename for the unwrapped result
        preferences = context.preferences.addons[__name__].preferences
        folder_path = (
            preferences.folder_path
        )  # Get the directory where Ministry of Flat is installed
        path = os.path.join(folder_path, "UnWrapConsole3.exe")
        os.system(f"{path} {fn} {fn2}")  # Run
        # STEP 3-3: Import the unwrapped obj file back into Blender
        bpy.ops.wm.obj_import(filepath=fn2)
        # STEP 3-4: Clean up (delete) the temporary files from steps 1 and 2
        os.remove(fn)
        os.remove(fn2)

        return {"FINISHED"}  # Done


def menu_func(self, context):
    self.layout.operator(AutoUV.bl_idname)  # Add item to the Object menu


def register():
    bpy.utils.register_class(MyAddonPreferences)
    bpy.utils.register_class(AutoUV)
    bpy.types.VIEW3D_MT_object.append(menu_func)  # Add item to the Object menu


def unregister():
    bpy.utils.unregister_class(MyAddonPreferences)
    bpy.utils.unregister_class(AutoUV)
    bpy.types.VIEW3D_MT_object.remove(menu_func)  # Remove item from the Object menu


if __name__ == "__main__":
    register()
