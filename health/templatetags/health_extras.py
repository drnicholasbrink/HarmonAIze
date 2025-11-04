from django import template

register = template.Library()

@register.filter
def lookup(dictionary, key):
    """Template filter to lookup dictionary values by key"""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None

@register.filter
def get_item(dictionary, key):
    """Alternative template filter for dictionary lookup"""
    return dictionary.get(key, None)
