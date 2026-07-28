// Deterministic font metrics manifest used by Feather layout clients.
#pragma once

#include <map>
#include <string>

#include "../common/fonts/types.h"

namespace typer::font_manifest {

std::string build(const std::map<std::string, const Font *> &fonts);

}  // namespace typer::font_manifest
