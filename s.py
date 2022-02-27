import copy
import fileinput
import os
import re
import yaml
from jinja2 import Template
import subprocess


def _camel_case(s):
    return "".join(map(lambda x: x.title(), s.split("_")))


def _snake_case(name):
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()


class Namespace:
    def __init__(self, name, types: dict):
        self.name = name
        self.types = types


class Reference:
    def __init__(self, name, path):
        self.name = name
        self.path = path


class Optional:
    def __init__(self, t):
        assert type(t) is not Optional
        self.under = t


class Primitive:
    map = {
        "str": "std::string",
        "bool": "bool",
        "int": "int32_t",
        "float": "double",
    }

    def __init__(self, name):
        self.name = name

    def header_name(self):
        return "string"

    def dir_name(self):
        assert False, "Do not use this method for Primitive"


class Object(Reference):
    def __init__(self, name, path, fields):
        super().__init__(name, path)
        self.fields = fields


class Array(Reference):
    def __init__(self, name, path, item):
        super().__init__(name, path)
        self.item = item


class EnumClass(Reference):
    def __init__(self, name, path, values):
        super().__init__(name, path)
        self.values = values


def _resolve_refpath(namespace, path):
    if "." in path:
        path = path.split(".")
        return path[-1], ".".join(path[:-1])
    return path, namespace.name


def create_types(namespace, name, desc):
    if namespace is None:
        namespace = Namespace(desc["namespace"], dict())
        for k, v in desc["types"].items():
            create_types(namespace, k, v)
        return namespace

    struct = copy.deepcopy(desc)
    if "enum" in struct:
        res = EnumClass(name, namespace.name, struct["enum"])
        namespace.types[res.name] = res
        return res
    if "structure" in struct:
        struct = struct["structure"]
    if type(struct) is str:
        if struct not in Primitive.map:
            return Reference(*_resolve_refpath(namespace, struct))
        res = Primitive(struct)
    if type(struct) is dict:
        fields = dict()
        for k, v in struct.items():
            if k[-1] == "?":
                k = k[:-1]
                v = Optional(create_types(namespace, name + _camel_case(k), v))
            else:
                v = create_types(namespace, name + _camel_case(k), v)
            fields[k] = v
        res = Object(name, namespace.name, fields)
    if type(struct) is list:
        assert len(struct) == 1, str(struct)
        res = Array(
            name, namespace.name, create_types(namespace, f"{name}Item", struct[0])
        )
    namespace.types[res.name] = res
    return res


types = []
for filename in fileinput.input():
    filename = filename.strip()
    with open(filename, "r") as f:
        got = yaml.safe_load(f)
    ns = create_types(None, None, got)
    types += ns.types.values()


# helper for rendering cpp files
class Cpp:
    def __init__(self, output_dir):
        self.output_dir = output_dir

    def definition_file(self, t):
        if type(t) is Primitive:
            return "string"
        return f"{self.dir(t.path)}/{_snake_case(t.name)}.hpp"

    def dir(self, path):
        return f'{self.output_dir}/{path.replace(".", "/")}'

    def includes(self, t):
        includes = set()
        if type(t) is Object:
            for v in t.fields.values():
                if type(v) is Optional:
                    v = v.under
                    includes.add("optional")
                includes.add(self.definition_file(v))
        if type(t) is Array:
            includes = set(["vector", self.definition_file(t.item)])
        return includes

    def typename(self, t):
        is_opt = False
        if type(t) is Optional:
            is_opt = True
            t = t.under
        res = None
        if type(t) is Primitive:
            res = Primitive.map[t.name]
        else:
            res = f'{t.path.replace(".", "::")}::{t.name}'
        if is_opt:
            res = f"std::optional<{res}>"
        return res

    def namespace(self, t):
        assert type(t) is not Primitive
        return t.path.replace(".", "::")

    def enum_value(self, v):
        return f"k{_camel_case(v)}"

    def enum_string_header_file(self, t):
        return f"{self.dir(t.path)}/{_snake_case(t.name)}_string_util.hpp"

    def enum_string_source_file(self, t):
        return f"{self.dir(t.path)}/{_snake_case(t.name)}_string_util.cpp"

    def format(self, filepath):
        subprocess.run(["clang-format", "-i", filepath])


def open_template(path):
    template = []
    with open(path, "r") as f:
        for line in f:
            template.append(line[:-1])
    return Template("\n".join(template))


definition_templates = {
    Object: open_template("templates/object.hpp.jinja"),
    Array: open_template("templates/array.hpp.jinja"),
    EnumClass: open_template("templates/enum.hpp.jinja"),
}

# TODO
class FileRenderer:
    def __init__(self, template_path):
        self.template = open_template(template_path)

    def render(self, t):
        return self.template.render(t=t, renderer=self)


enum_string_util_hpp_template = open_template("templates/enum_string.hpp.jinja")
enum_string_util_cpp_template = open_template("templates/enum_string.cpp.jinja")

cpp = Cpp(output_dir="generated")
for t in types:
    if type(t) not in definition_templates:
        continue

    os.makedirs(cpp.dir(t.path), exist_ok=True)
    with open(cpp.definition_file(t), "w") as f:
        print(definition_templates[type(t)].render(t=t, cpp=cpp), file=f)
    cpp.format(cpp.definition_file(t))

    if type(t) is EnumClass:
        with open(cpp.enum_string_header_file(t), "w") as f:
            print(enum_string_util_hpp_template.render(t=t, cpp=cpp), file=f)
        cpp.format(cpp.enum_string_header_file(t))
        with open(cpp.enum_string_source_file(t), "w") as f:
            print(enum_string_util_cpp_template.render(t=t, cpp=cpp), file=f)
        cpp.format(cpp.enum_string_source_file(t))
