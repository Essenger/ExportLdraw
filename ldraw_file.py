import os
import mathutils
import re

from . import options
from . import filesystem
from . import helpers
from . import ldraw_part_types

from .ldraw_node import LDrawNode
from .ldraw_geometry import LDrawGeometry
from .texmap import TexMap
from . import special_bricks
from . import ldraw_colors
from . import ldraw_camera

mpd_file_cache = {}
file_cache = {}


def reset_caches():
    global mpd_file_cache
    global file_cache

    mpd_file_cache = {}
    file_cache = {}


def read_color_table():
    ldraw_colors.reset_caches()

    """Reads the color values from the LDConfig.ldr file. For details of the
    Ldraw color system see: http://www.ldraw.org/article/547"""

    if options.use_alt_colors:
        filepath = "LDCfgalt.ldr"
    else:
        filepath = "LDConfig.ldr"

    ldraw_file = LDrawFile(filepath)
    ldraw_file.read_file()
    ldraw_file.parse_file()


def handle_mpd(filepath):
    ldraw_file = LDrawFile(filepath)
    ldraw_file.read_file()

    lines = ldraw_file.lines

    if len(lines) < 1:
        return None

    if not lines[0].lower().startswith("0 f"):
        return filepath

    root_file = None
    current_file = None
    for line in lines:
        params = helpers.parse_line(line, 9)

        if params is None:
            continue

        if params[0] == "0" and params[1].lower() == "file":
            __parse_current_file(current_file)
            current_file = LDrawFile(line[7:].lower())

            if root_file is None:
                root_file = line[7:].lower()

        elif params[0] == "0" and params[1].lower() == "nofile":
            __parse_current_file(current_file)
            current_file = None

        elif current_file is not None:
            current_file.lines.append(line)

    __parse_current_file(current_file)

    if root_file is not None:
        return root_file
    return filepath


def __parse_current_file(ldraw_file):
    if ldraw_file is not None:
        mpd_file_cache[ldraw_file.filepath] = ldraw_file


def get_child_node(line, params):
    color_code = params[1]

    (x, y, z, a, b, c, d, e, f, g, h, i) = map(float, params[2:14])
    matrix = mathutils.Matrix((
        (a, b, c, x),
        (d, e, f, y),
        (g, h, i, z),
        (0, 0, 0, 1)
    ))

    # there might be spaces in the filename, so don't just split on whitespace
    # filename_args = re.search(r"(?:.*\s+){14}(.*)", line.strip())
    # print(line.strip())
    filename_args = re.search(r".*?\s+.*?\s+.*?\s+.*?\s+.*?\s+.*?\s+.*?\s+.*?\s+.*?\s+.*?\s+.*?\s+.*?\s+.*?\s+.*?\s+(.*)", line.strip())
    filename = filename_args[1].lower()
    # filename = " ".join(params[14:]).lower()

    if options.display_logo:
        if filename in special_bricks.studs:
            parts = filename.split(".")
            name = parts[0]
            ext = parts[1]
            new_filename = f"{name}-{options.chosen_logo}.{ext}"
            if filesystem.locate(new_filename):
                filename = new_filename

    key = []
    key.append(options.resolution)
    if options.display_logo:
        key.append(options.chosen_logo)
    if options.remove_doubles:
        key.append("rd")
    key.append(color_code)
    key.append(os.path.basename(filename))
    key = "_".join([k.lower() for k in key])
    key = re.sub(r"[^a-z0-9._]", "-", key)

    if key not in file_cache:
        ldraw_file = LDrawFile(filename)
        if not ldraw_file.read_file():
            return None
        ldraw_file.parse_file()
        file_cache[key] = ldraw_file
    ldraw_file = file_cache[key]
    ldraw_node = LDrawNode(ldraw_file, color_code=color_code, matrix=matrix)

    return ldraw_node


class LDrawFile:
    def __init__(self, filepath):
        self.filepath = filepath
        self.name = ""
        self.child_nodes = []
        self.geometry = LDrawGeometry()
        self.part_type = None
        self.lines = []

    def read_file(self):
        if self.filepath in mpd_file_cache:
            self.lines = mpd_file_cache[self.filepath].lines
        else:
            # if missing, use a,b,c etc parts if available
            # TODO: look in this file's directory and directories relative to this file's directory
            filepath = filesystem.locate(self.filepath)
            if filepath is None:
                print(f"missing {self.filepath}")
                return False
            self.lines = filesystem.read_file(filepath)
        return True

    def parse_file(self):
        if len(self.lines) < 1:
            return

        camera = None
        texmap_start = False
        texmap_next = False
        texmap_fallback = False

        for line in self.lines:
            params = helpers.parse_line(line, 15)

            if params is None:
                continue

            if params[0] == "0":
                if params[1].lower() in ["!colour"]:
                    ldraw_colors.parse_color(params)
                elif params[1].lower() in ["!ldraw_org"]:
                    if params[2].lower() in ["lcad"]:
                        self.part_type = params[3].lower()
                    else:
                        self.part_type = params[2].lower()
                elif params[1].lower() == "name:":
                    self.name = line[7:].lower().strip()
                elif params[1].lower() in ["step"]:
                    if options.meta_step:
                        ldraw_node = LDrawNode(None)
                        ldraw_node.meta_command = params[1].lower()
                        self.child_nodes.append(ldraw_node)
                        texmap_start, texmap_next, texmap_fallback = self.set_texmap_end()
                elif params[1].lower() in ["save"]:
                    if options.meta_save:
                        ldraw_node = LDrawNode(None)
                        ldraw_node.meta_command = params[1].lower()
                        self.child_nodes.append(ldraw_node)
                elif params[1].lower() in ["clear"]:
                    if options.meta_clear:
                        ldraw_node = LDrawNode(None)
                        ldraw_node.meta_command = params[1].lower()
                        self.child_nodes.append(ldraw_node)
                elif params[1].lower() in ["print", "write"]:
                    if options.meta_print_write:
                        print(line[7:].strip())
                elif params[1].lower() in ["!ldcad"]:  # http://www.melkert.net/LDCad/tech/meta
                    if params[2].lower() in ["group_def"]:
                        params = re.search(r"\S+\s+\S+\s+\S+\s+(\[.*\])\s+(\[.*\])\s+(\[.*\])\s+(\[.*\])\s+(\[.*\])", line.strip())

                        ldraw_node = LDrawNode(None)
                        ldraw_node.meta_command = "group_def"

                        id_args = re.search(r"\[(.*)=(.*)\]", params[2])
                        ldraw_node.meta_args["id"] = id_args[2]

                        name_args = re.search(r"\[(.*)=(.*)\]", params[4])
                        ldraw_node.meta_args["name"] = name_args[2]

                        self.child_nodes.append(ldraw_node)
                    elif params[2].lower() in ["group_nxt"]:
                        params = re.search(r"\S+\s+\S+\s+\S+\s+(\[.*\])\s+(\[.*\])", line.strip())

                        ldraw_node = LDrawNode(None)
                        ldraw_node.meta_command = "group_nxt"

                        id_args = re.search(r"\[(.*)=(.*)\]", params[1])
                        ldraw_node.meta_args["id"] = id_args[2]

                        self.child_nodes.append(ldraw_node)
                elif params[1].lower() in ["!leocad"]:  # https://www.leocad.org/docs/meta.html
                    if params[2].lower() in ["group"]:
                        if params[3].lower() in ["begin"]:
                            # begin_params = re.search(r"(?:.*\s+){3}begin\s+(.*)", line, re.IGNORECASE)
                            begin_params = re.search(r"\S+\s+\S+\s+\S+\s+\S+\s+(.*)", line.strip())

                            if begin_params is not None:
                                ldraw_node = LDrawNode(None)
                                ldraw_node.meta_command = "group_begin"
                                ldraw_node.meta_args["name"] = begin_params[1]
                                self.child_nodes.append(ldraw_node)
                        elif params[3].lower() in ["end"]:
                            ldraw_node = LDrawNode(None)
                            ldraw_node.meta_command = "group_end"
                            self.child_nodes.append(ldraw_node)
                    elif params[2] == "CAMERA":
                        if camera is None:
                            camera = ldraw_camera.LDrawCamera()

                        params = params[3:]

                        # https://www.leocad.org/docs/meta.html
                        # "Camera commands can be grouped in the same line"
                        # params = params[1:] at the end bumps promotes params[2] to params[1]

                        while len(params) > 0:
                            if params[0] == "FOV":
                                camera.fov = float(params[1])
                                params = params[2:]
                            elif params[0] == "ZNEAR":
                                scale = 1.0
                                camera.z_near = scale * float(params[1])
                                params = params[2:]
                            elif params[0] == "ZFAR":
                                scale = 1.0
                                camera.z_far = scale * float(params[1])
                                params = params[2:]
                            elif params[0] in ["POSITION", "TARGET_POSITION", "UP_VECTOR"]:
                                (x, y, z) = map(float, params[1:4])
                                vector = mathutils.Vector((x, y, z))

                                if params[0] == "POSITION":
                                    camera.position = vector
                                    if options.debug_text:
                                        print("POSITION")
                                        print(camera.position)
                                        print(camera.target_position)
                                        print(camera.up_vector)

                                elif params[0] == "TARGET_POSITION":
                                    camera.target_position = vector
                                    if options.debug_text:
                                        print("TARGET_POSITION")
                                        print(camera.position)
                                        print(camera.target_position)
                                        print(camera.up_vector)

                                elif params[0] == "UP_VECTOR":
                                    camera.up_vector = vector
                                    if options.debug_text:
                                        print("UP_VECTOR")
                                        print(camera.position)
                                        print(camera.target_position)
                                        print(camera.up_vector)

                                params = params[4:]

                            elif params[0] == "ORTHOGRAPHIC":
                                camera.orthographic = True
                                params = params[1:]
                            elif params[0] == "HIDDEN":
                                camera.hidden = True
                                params = params[1:]
                            elif params[0] == "NAME":
                                camera_name_params = re.search(r"\S+\s+\S+\s+\S+\s+\S+\s+(.*)", line.strip())

                                camera.name = camera_name_params[1].strip()

                                # By definition this is the last of the parameters
                                params = []

                                ldraw_camera.cameras.append(camera)
                                camera = None
                            else:
                                params = params[1:]
                elif texmap_next:
                    texmap_start, texmap_next, texmap_fallback = self.set_texmap_end()
                    # also error
                elif params[1].lower() in ["!texmap"]:  # https://www.ldraw.org/documentation/ldraw-org-file-format-standards/language-extension-for-texture-mapping.html
                    do_texmaps = True
                    if not do_texmaps:
                        continue
                    if params[2].lower() in ["start", "next"]:
                        ldraw_node = LDrawNode(None)
                        if params[2].lower() == "start":
                            texmap_start = True
                            ldraw_node.meta_command = "texmap_start"
                        elif params[2].lower() == "next":
                            texmap_next = True
                            ldraw_node.meta_command = "texmap_next"
                        texmap_fallback = False

                        (x1, y1, z1, x2, y2, z2, x3, y3, z3) = map(float, params[4:13])

                        ldraw_node.meta_args["texmap"] = TexMap({
                            "method": params[3].lower(),
                            "parameters": [
                                mathutils.Vector((x1, y1, z1)),
                                mathutils.Vector((x2, y2, z2)),
                                mathutils.Vector((x3, y3, z3)),
                            ],
                            "texmap": params[13],
                            "glossmap": params[14],
                        })

                        self.child_nodes.append(ldraw_node)
                    elif texmap_start:
                        if params[2].lower() in ["fallback"]:
                            texmap_fallback = True
                        elif params[2].lower() in ["end"]:
                            texmap_start, texmap_next, texmap_fallback = self.set_texmap_end()
                elif params[1].lower() in ["!:"] and texmap_start:
                    # remove 0 !: from line so that it can be parsed like a normal line
                    clean_line = re.sub(r"(.*?\s+!:\s+)", "", line)
                    clean_params = params[2:]
                    self.parse_geometry_line(clean_line, clean_params)
                    if texmap_next:
                        texmap_start, texmap_next, texmap_fallback = self.set_texmap_end()
            else:
                if not (texmap_start and texmap_fallback):
                    self.parse_geometry_line(line, params)

        if self.name == "":
            self.name = os.path.basename(self.filepath)

    def set_texmap_end(self):
        ldraw_node = LDrawNode(None)
        ldraw_node.meta_command = "texmap_end"
        self.child_nodes.append(ldraw_node)

        texmap_start = False
        texmap_next = False
        texmap_fallback = False
        return texmap_start, texmap_next, texmap_fallback

    def parse_geometry_line(self, line, params):
        if params[0] == "1":
            child_node = get_child_node(line, params)
            if child_node is None:
                return False
            self.child_nodes.append(child_node)
            if self.part_type in ldraw_part_types.model_types:
                if child_node.file.part_type in ldraw_part_types.subpart_types:
                    self.part_type = "part"
        elif params[0] in ["2"]:
            self.geometry.parse_edge(params)
            if self.part_type in ldraw_part_types.model_types:
                self.part_type = "part"
        elif params[0] in ["3", "4"]:
            self.geometry.parse_face(params)
            if self.part_type in ldraw_part_types.model_types:
                self.part_type = "part"
