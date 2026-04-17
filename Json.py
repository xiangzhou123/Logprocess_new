import json
import re
import sys

def fix_json_file(input_file, output_file="salida.json"):
    # Leer contenido completo
    with open(input_file, "r", encoding="utf-16") as f:
        content = f.read()

    # Buscar objetos JSON usando regex que detecta cada {...}
    objetos = re.findall(r'\{(?:[^{}]|(?R))*\}', content)

    if not objetos:
        print("No se encontraron objetos JSON.")
        return

    json_list = []

    for obj in objetos:
        try:
            json_list.append(json.loads(obj))
        except json.JSONDecodeError as e:
            print(f"Error al parsear objeto:\n{obj[:100]}...\n{e}")
    
    # Guardar como array JSON válido
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(json_list, f, indent=2, ensure_ascii=False)

    print(f"Archivo convertido correctamente → {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python fix_json.py <archivo_entrada>")
        sys.exit(1)

    fix_json_file(sys.argv[1])
