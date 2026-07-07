import os
import streamlit.components.v1 as components

_component_func = components.declare_component(
    "date_range_picker",
    path=os.path.dirname(os.path.abspath(__file__)),
)


def date_range_picker(date_from, date_to, key=None):
    result = _component_func(
        date_from=str(date_from),
        date_to=str(date_to),
        key=key,
        default={"from": str(date_from), "to": str(date_to)},
    )
    return result
