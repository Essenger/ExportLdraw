import os
import bpy
import math
import mathutils
import bmesh

from . import strings
from . import options
from . import matrices
from . import ldraw_part_types

from .ldraw_geometry import LDrawGeometry
from .face_info import FaceInfo
from . import blender_materials
from . import ldraw_colors
from . import special_bricks

part_count = 0
current_step = 0
last_frame = 0
face_info_cache = {}
geometry_cache = {}
top_collection = None
top_empty = None
gap_scale_empty = None
collection_id_map = {}
next_collection = None
end_next_collection = False
texmaps = []
current_texmap = None


def reset_caches():
    global part_count
    global current_step
    global last_frame
    global face_info_cache
    global geometry_cache
    global top_collection
    global top_empty
    global gap_scale_empty
    global collection_id_map
    global next_collection
    global end_next_collection
    global texmaps

    part_count = 0
    current_step = 0
    last_frame = 0
    face_info_cache = {}
    geometry_cache = {}
    top_collection = None
    top_empty = None
    gap_scale_empty = None
    collection_id_map = {}
    next_collection = None
    end_next_collection = False
    texmaps = []

    if options.meta_step:
        set_step()


def set_step():
    start_frame = options.starting_step_frame
    frame_length = options.frames_per_step
    global last_frame
    last_frame = (start_frame + frame_length) + (frame_length * current_step)
    if options.set_timelime_markers:
        bpy.context.scene.timeline_markers.new("STEP", frame=last_frame)


def create_meta_group(key, parent_collection):
    if key not in bpy.data.collections:
        bpy.data.collections.new(key)
    collection = bpy.data.collections[key]
    if parent_collection is None:
        parent_collection = bpy.context.scene.collection
    if collection.name not in parent_collection.children:
        parent_collection.children.link(collection)


def apply_slope_materials(mesh, filename):
    if len(mesh.materials) > 0:
        for f in mesh.polygons:
            face_material = mesh.materials[f.material_index]

            if strings.ldraw_color_code_key not in face_material:
                continue

            color_code = str(face_material[strings.ldraw_color_code_key])
            color = ldraw_colors.get_color(color_code)

            is_slope_material = special_bricks.is_slope_face(filename, f)
            material = blender_materials.get_material(color, is_slope_material=is_slope_material)
            if material is None:
                continue

            if material.name not in mesh.materials:
                mesh.materials.append(material)
            f.material_index = mesh.materials.find(material.name)


def create_object(mesh, parent_matrix, matrix):
    obj = bpy.data.objects.new(mesh.name, mesh)

    if top_empty is None:
        obj.matrix_world = matrices.scaled_matrix(options.import_scale) @ matrices.rotation @ parent_matrix @ matrix
        if options.make_gaps and options.gap_target == "object":
            obj.matrix_world = obj.matrix_world @ matrices.scaled_matrix(options.gap_scale)
    else:
        obj.matrix_world = parent_matrix @ matrix

        if options.make_gaps and options.gap_target == "object":
            if options.gap_scale_strategy == "object":
                obj.matrix_world = obj.matrix_world @ matrices.scaled_matrix(options.gap_scale)
            elif options.gap_scale_strategy == "constraint":
                global gap_scale_empty
                if gap_scale_empty is None and top_collection is not None:
                    gap_scale_empty = bpy.data.objects.new("gap_scale", None)
                    gap_scale_empty.matrix_world = gap_scale_empty.matrix_world @ matrices.scaled_matrix(options.gap_scale)
                    top_collection.objects.link(gap_scale_empty)
                    if options.debug_text:
                        print(gap_scale_empty.name)
                copy_constraint = obj.constraints.new("COPY_SCALE")
                copy_constraint.target = gap_scale_empty
                copy_constraint.target.parent = top_empty

        obj.parent = top_empty  # must be after matrix_world set or else transform is incorrect

    # https://docs.blender.org/api/current/bpy.types.bpy_struct.html#bpy.types.bpy_struct.keyframe_insert
    # https://docs.blender.org/api/current/bpy.types.Scene.html?highlight=frame_set#bpy.types.Scene.frame_set
    # https://docs.blender.org/api/current/bpy.types.Object.html?highlight=rotation_quaternion#bpy.types.Object.rotation_quaternion
    if options.meta_step:
        if options.debug_text:
            print(current_step)

        bpy.context.scene.frame_set(options.starting_step_frame)
        obj.hide_viewport = True
        obj.hide_render = True
        obj.keyframe_insert(data_path="hide_render")
        obj.keyframe_insert(data_path="hide_viewport")

        bpy.context.scene.frame_set(last_frame)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.keyframe_insert(data_path="hide_render")
        obj.keyframe_insert(data_path="hide_viewport")

        if options.debug_text:
            print(last_frame)

    if options.smooth_type == "edge_split":
        edge_modifier = obj.modifiers.new("Edge Split", type='EDGE_SPLIT')
        edge_modifier.use_edge_angle = True
        edge_modifier.split_angle = math.radians(89.9)  # 1.56905 - 89.9 so 90 degrees and up are affected
        edge_modifier.use_edge_sharp = True

    if options.bevel_edges:
        bevel_modifier = obj.modifiers.new("Bevel", type='BEVEL')
        bevel_modifier.width = 0.10
        bevel_modifier.segments = 4
        bevel_modifier.profile = 0.5
        bevel_modifier.limit_method = "WEIGHT"
        bevel_modifier.use_clamp_overlap = True

    return obj


# https://youtu.be/cQ0qtcSymDI?t=356
# https://www.youtube.com/watch?v=cQ0qtcSymDI&t=0s
# https://www.blenderguru.com/articles/cycles-input-encyclopedia
# https://blenderscripting.blogspot.com/2011/05/blender-25-python-moving-object-origin.html
# https://blenderartists.org/t/how-to-set-origin-to-center/687111
# https://blenderartists.org/t/modifying-object-origin-with-python/507305/3
# https://blender.stackexchange.com/questions/414/how-to-use-bmesh-to-add-verts-faces-and-edges-to-existing-geometry
# https://devtalk.blender.org/t/bmesh-adding-new-verts/11108/2
# f1 = Vector((rand(-5, 5),rand(-5, 5),rand(-5, 5)))
# f2 = Vector((rand(-5, 5),rand(-5, 5),rand(-5, 5)))
# f3 = Vector((rand(-5, 5),rand(-5, 5),rand(-5, 5)))
# f = [f1, f2, f3]
# for f in enumerate(faces):
#     this_vert = bm.verts.new(f)
def do_create_mesh(key, geometry_vertices, geometry_faces):
    vertices = [v.to_tuple() for v in geometry_vertices]
    edges = []
    faces = []

    # makes indexes sequential
    face_index = 0
    for f in geometry_faces:
        new_face = []
        for _ in range(f):
            new_face.append(face_index)
            face_index += 1
        faces.append(new_face)

    # add materials before doing from_pydata step
    mesh = bpy.data.meshes.new(key)
    mesh.from_pydata(vertices, edges, faces)
    mesh.validate()
    mesh.update(calc_edges=True)

    return mesh


def create_edge_mesh(key, geometry):
    return do_create_mesh(key, geometry.edge_vertices, geometry.edges)


def create_mesh(key, geometry):
    return do_create_mesh(key, geometry.vertices, geometry.faces)


# https://blender.stackexchange.com/a/91687
# for f in bm.faces:
#     f.smooth = True
# mesh = context.object.data
# for f in mesh.polygons:
#     f.use_smooth = True
# values = [True] * len(mesh.polygons)
# mesh.polygons.foreach_set("use_smooth", values)
# bpy.context.object.active_material.use_backface_culling = True
# bpy.context.object.active_material.use_screen_refraction = True
def apply_materials(mesh, geometry):
    for i, f in enumerate(mesh.polygons):
        face_info = geometry.face_info[i]

        grain_slope_allowed = face_info.grain_slope_allowed

        color_code = face_info.color_code
        color = ldraw_colors.get_color(color_code)

        use_edge_color = face_info.use_edge_color
        material = blender_materials.get_material(color, use_edge_color=use_edge_color)
        if material is None:
            continue

        if material.name not in mesh.materials:
            mesh.materials.append(material)
        f.material_index = mesh.materials.find(material.name)

        f.use_smooth = options.shade_smooth


def bmesh_ops(mesh, geometry):
    if options.bevel_edges:
        mesh.use_customdata_edge_bevel = True

    bm = bmesh.new()
    bm.from_mesh(mesh)

    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    if options.remove_doubles:
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=options.merge_distance)

    # Find layer for bevel weights
    bevel_weight_layer = None
    if options.bevel_edges:
        if 'BevelWeight' in bm.edges.layers.bevel_weight:
            bevel_weight_layer = bm.edges.layers.bevel_weight["BevelWeight"]

    if options.recalculate_normals:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])

    # Create kd tree for fast "find nearest points" calculation
    kd = mathutils.kdtree.KDTree(len(bm.verts))
    for i, v in enumerate(bm.verts):
        kd.insert(v.co, i)
    kd.balance()
    # Create edgeIndices dictionary, which is the list of edges as pairs of indicies into our bm.verts array
    edge_indices = {}
    for i in range(0, len(geometry.edge_vertices), 2):
        edges0 = [index for (co, index, dist) in kd.find_range(geometry.edge_vertices[i + 0], options.merge_distance)]
        edges1 = [index for (co, index, dist) in kd.find_range(geometry.edge_vertices[i + 1], options.merge_distance)]

        for e0 in edges0:
            for e1 in edges1:
                edge_indices[(e0, e1)] = True
                edge_indices[(e1, e0)] = True

    # Find the appropriate mesh edges and make them sharp (i.e. not smooth)
    for edge_vertex in bm.edges:
        v0 = edge_vertex.verts[0].index
        v1 = edge_vertex.verts[1].index
        if (v0, v1) in edge_indices:
            # Make edge sharp
            edge_vertex.smooth = False

            # Add bevel weight
            if bevel_weight_layer is not None:
                bevel_wight = 1.0
                edge_vertex[bevel_weight_layer] = bevel_wight

    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    bm.to_mesh(mesh)

    bm.clear()
    bm.free()


def create_gp_mesh(key, mesh):
    gp_mesh = bpy.data.grease_pencils.new(key)

    gp_mesh.pixel_factor = 5.0
    gp_mesh.stroke_depth_order = "3D"

    gp_layer = gp_mesh.layers.new("gpl")
    gp_frame = gp_layer.frames.new(1)
    gp_layer.active_frame = gp_frame

    for e in mesh.edges:
        gp_stroke = gp_frame.strokes.new()
        gp_stroke.material_index = 0
        gp_stroke.line_width = 10.0
        for v in e.vertices:
            i = len(gp_stroke.points)
            gp_stroke.points.add(1)
            gp_point = gp_stroke.points[i]
            gp_point.co = mesh.vertices[v].co

    return gp_mesh


def apply_gp_materials(gp_mesh):
    color_code = "0"
    color = ldraw_colors.get_color(color_code)

    use_edge_color = True
    base_material = blender_materials.get_material(color, use_edge_color=use_edge_color)
    if base_material is None:
        return

    material_name = f"gp_{base_material.name}"
    if material_name not in bpy.data.materials:
        material = base_material.copy()
        material.name = material_name
        bpy.data.materials.create_gpencil_data(material)  # https://developer.blender.org/T67102
    material = bpy.data.materials[material_name]
    gp_mesh.materials.append(material)


class LDrawNode:
    def __init__(self, file, color_code="16", matrix=matrices.identity):
        self.file = file
        self.color_code = color_code
        self.matrix = matrix
        self.top = False
        self.meta_command = None
        self.meta_args = {}

    def load(self, parent_matrix=matrices.identity, parent_color_code="16", geometry=None, is_stud=False, is_edge_logo=False, parent_collection=None):
        global current_step
        global top_collection
        global top_empty
        global next_collection
        global end_next_collection

        if self.file is None:
            if self.meta_command == "step":
                current_step += 1
                set_step()
            elif self.meta_command == "group_begin":
                create_meta_group(self.meta_args["name"], parent_collection)
                end_next_collection = False
                if self.meta_args["name"] in bpy.data.collections:
                    next_collection = bpy.data.collections[self.meta_args["name"]]
            elif self.meta_command == "group_end":
                end_next_collection = True
            elif self.meta_command == "group_def":
                if self.meta_args["id"] not in collection_id_map:
                    collection_id_map[self.meta_args["id"]] = self.meta_args["name"]
                create_meta_group(self.meta_args["name"], parent_collection)
            elif self.meta_command == "group_nxt":
                if self.meta_args["id"] in collection_id_map:
                    key = collection_id_map[self.meta_args["id"]]
                    if key in bpy.data.collections:
                        next_collection = bpy.data.collections[key]
                end_next_collection = True
            elif self.meta_command == "save":
                if options.set_timelime_markers:
                    bpy.context.scene.timeline_markers.new("SAVE", frame=last_frame)
            elif self.meta_command == "clear":
                if options.set_timelime_markers:
                    bpy.context.scene.timeline_markers.new("CLEAR", frame=last_frame)
                if top_collection is not None:
                    for ob in top_collection.all_objects:
                        bpy.context.scene.frame_set(last_frame)
                        ob.hide_viewport = True
                        ob.hide_render = True
                        ob.keyframe_insert(data_path="hide_render")
                        ob.keyframe_insert(data_path="hide_viewport")
            elif self.meta_command == "texmap_start":
                global current_texmap
                if current_texmap is not None:
                    texmaps.append(current_texmap)
                current_texmap = self.meta_args["texmap"]
            return

        if options.no_studs and self.file.name.startswith("stud"):
            return

        if self.color_code != "16":
            parent_color_code = self.color_code

        key = []
        key.append(options.resolution)
        key.append(parent_color_code)
        if options.display_logo:
            key.append(options.chosen_logo)
        if options.remove_doubles:
            key.append("rd")
        if options.smooth_type == "auto_smooth":
            key.append("as")
        if options.smooth_type == "edge_split":
            key.append("es")
        if options.use_alt_colors:
            key.append("alt")
        if options.add_subsurface:
            key.append("ss")
        if parent_color_code == "24":
            key.append("edge")
        key.append(self.file.name)
        key = "_".join([k.lower() for k in key])

        is_model = self.file.part_type in ldraw_part_types.model_types
        is_part = self.file.part_type in ldraw_part_types.part_types
        is_shortcut = self.file.part_type in ldraw_part_types.shortcut_types
        is_subpart = self.file.part_type in ldraw_part_types.subpart_types

        matrix = parent_matrix @ self.matrix
        file_collection = parent_collection

        if is_model:
            file_collection = bpy.data.collections.new(os.path.basename(self.file.filepath))
            if parent_collection is not None:
                parent_collection.children.link(file_collection)

            if top_collection is None:
                top_collection = file_collection
                if options.debug_text:
                    print(top_collection.name)

                if options.parent_to_empty and top_empty is None:
                    top_empty = bpy.data.objects.new(top_collection.name, None)
                    top_empty.matrix_world = top_empty.matrix_world @ matrices.rotation @ matrices.scaled_matrix(options.import_scale)
                    if top_collection is not None:
                        top_collection.objects.link(top_empty)
                    if options.debug_text:
                        print(top_empty.name)
        elif geometry is None:
            geometry = LDrawGeometry()
            matrix = matrices.identity
            self.top = True
            global part_count
            part_count += 1

        if options.meta_group and next_collection is not None:
            file_collection = next_collection
            if end_next_collection:
                next_collection = None

        if options.debug_text:
            print("===========")
            if is_model:
                print("is_model")
            if is_part:
                print("is_part")
            elif is_shortcut:
                print("is_shortcut")
            elif is_subpart:
                print("is_subpart")
            print(self.file.name)
            print("===========")

        # if it's a part and already in the cache, reuse it
        # meta commands are not in self.top files which is how they are counted
        if self.top and key in geometry_cache:
            geometry = geometry_cache[key]
        else:
            if geometry is not None:
                if self.file.name in ["stud.dat", "stud2.dat"]:
                    is_stud = True

                # ["logo.dat", "logo2.dat", "logo3.dat", "logo4.dat", "logo5.dat"]
                if self.file.name in ["logo.dat", "logo2.dat"]:
                    is_edge_logo = True

                vertices = [matrix @ v for v in self.file.geometry.vertices]
                geometry.vertices.extend(vertices)

                if (not is_edge_logo) or (is_edge_logo and options.display_logo):
                    vertices = [matrix @ v for v in self.file.geometry.edge_vertices]
                    geometry.edge_vertices.extend(vertices)

                geometry.edges.extend(self.file.geometry.edges)
                geometry.faces.extend(self.file.geometry.faces)

                if key not in face_info_cache:
                    new_face_info = []
                    for face_info in self.file.geometry.face_info:
                        copy = FaceInfo(color_code=parent_color_code, grain_slope_allowed=not is_stud)
                        if parent_color_code == "24":
                            copy.use_edge_color = True
                        if face_info.color_code != "16":
                            copy.color_code = face_info.color_code
                        new_face_info.append(copy)
                    face_info_cache[key] = new_face_info
                new_face_info = face_info_cache[key]
                geometry.face_info.extend(new_face_info)

            for child in self.file.child_nodes:
                child.load(parent_matrix=matrix,
                           parent_color_code=parent_color_code,
                           geometry=geometry,
                           is_stud=is_stud,
                           is_edge_logo=is_edge_logo,
                           parent_collection=file_collection)

            if self.top:
                geometry_cache[key] = geometry

        if self.top:
            if key not in bpy.data.meshes:
                mesh = create_mesh(key, geometry)

                # apply materials to mesh
                # then mesh cleanup
                # then apply slope materials
                # this order is important because bmesh_ops causes
                # mesh.polygons to get out of sync geometry.face_info
                # which causes materials and slop materials to be applied incorrectly
                apply_materials(mesh, geometry)  # combine with create_mesh
                bmesh_ops(mesh, geometry)
                apply_slope_materials(mesh, self.file.name)

                if options.smooth_type == "auto_smooth":
                    mesh.use_auto_smooth = options.shade_smooth
                    mesh.auto_smooth_angle = math.radians(89.9)  # 1.56905 - 89.9 so 90 degrees and up are affected
                if options.make_gaps and options.gap_target == "mesh":
                    mesh.transform(matrices.scaled_matrix(options.gap_scale))
                mesh[strings.ldraw_filename_key] = self.file.name
            mesh = bpy.data.meshes[key]

            if options.import_edges:
                e_key = f"e_{key}"
                if e_key not in bpy.data.meshes:
                    edge_mesh = create_edge_mesh(e_key, geometry)
                    edge_mesh[strings.ldraw_edge_key] = self.file.name
                    if options.make_gaps and options.gap_target == "mesh":
                        edge_mesh.transform(matrices.scaled_matrix(options.gap_scale))
                edge_mesh = bpy.data.meshes[e_key]

                if options.grease_pencil_edges:
                    gp_mesh = create_gp_mesh(key, edge_mesh)
                    apply_gp_materials(gp_mesh)
                    gp_object = bpy.data.objects.new(key, gp_mesh)
                    gp_object.matrix_world = parent_matrix @ self.matrix
                    gp_object.active_material_index = len(gp_mesh.materials)

                    collection_name = "Grease Pencil Edges"
                    if collection_name not in bpy.context.scene.collection.children:
                        collection = bpy.data.collections.new(collection_name)
                        bpy.context.scene.collection.children.link(collection)
                    collection = bpy.context.scene.collection.children[collection_name]
                    collection.objects.link(gp_object)

            obj = create_object(mesh, parent_matrix, self.matrix)
            obj[strings.ldraw_filename_key] = self.file.name

            if file_collection is not None:
                file_collection.objects.link(obj)
            else:
                bpy.context.scene.collection.objects.link(obj)
