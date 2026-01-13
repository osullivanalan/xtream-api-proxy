import json
import os

def load_config():
    # Fix for file path loading
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "config.json")
    with open(config_path, "r") as f:
        return json.load(f)

def apply_filters(data, content_type):
    """
    Filters data ensuring the category starts with specific prefixes.
    """
    config = load_config()
    allowed_prefixes = config["filters"].get(content_type, [])
    
    # If no filters are defined for this type, return everything
    if not allowed_prefixes:
        return data

    filtered_data = []
    
    # Pre-process prefixes to uppercase to make comparison faster/case-insensitive
    # We strip whitespace to ensure " EN " in config matches "EN" in data
    clean_prefixes = tuple(p.strip().upper() for p in allowed_prefixes)
    
    for item in data:
        # 1. Try to find the category name
        cat_name = item.get("category_name", "")
        
        # Fallback: some providers use 'category_str' or just 'name'
        if not cat_name: 
            cat_name = item.get("name", "")

        if not cat_name:
            continue

        # 2. Clean the category name from the API
        # We strip whitespace from the start so "  EN | Movies" is treated as "EN | Movies"
        clean_cat_name = str(cat_name).strip().upper()

        # 3. Strict Prefix Check
        # startswith() can accept a tuple of strings to check against
        if clean_cat_name.startswith(clean_prefixes):
            filtered_data.append(item)
            
    return filtered_data
