## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license

"""Explicit bounded source-generation contract for reusable declarations."""


class ReusableSourceContract:
    strategy = "core.reusable_declarations"
    supported_forms = ("module_declarations", "direct_page_call", "stylesheet_extension", "typed_template_calls")
    parameter_source = "creation_property_fields"
    style_source = "styleable_property_and_layout_fields"

    def as_dict(self):
        return {"strategy": self.strategy,
                "supported_forms": list(self.supported_forms),
                "parameter_source": self.parameter_source,
                "style_source": self.style_source}


REUSABLE_SOURCE_CONTRACT = ReusableSourceContract()
