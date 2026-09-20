#include "../font_manifest.h"
#include "../../common/fonts/JetBrainsMono12ptb2.h"
#include "../../common/fonts/Roboto12pt.h"
#include "test_runner.h"

#include <map>
#include <string>

namespace {

const std::map<std::string, const Font *> fonts{
    {Roboto12pt.name, &Roboto12pt},
    {JetBrainsMono12ptb2.name, &JetBrainsMono12ptb2},
};

void schema_and_sorting() {
    const auto manifest = typer::font_manifest::build(fonts);
    TYPER_CHECK(manifest.starts_with(
        "{\"schema\":\"font-metrics/v1\",\"wrap_algorithm\":\"word-v1\",\"fonts\":["));
    TYPER_CHECK(manifest.ends_with("]}\n"));
    TYPER_CHECK(manifest.find("JetBrainsMono 12pt")
                < manifest.find("Roboto 12pt"));
}

void metrics_ranges_and_monospacing() {
    const auto manifest = typer::font_manifest::build(fonts);
    const auto mono = manifest.find("\"name\":\"JetBrainsMono 12pt\"");
    const auto roboto = manifest.find("\"name\":\"Roboto 12pt\"");
    TYPER_CHECK(mono != std::string::npos);
    TYPER_CHECK(roboto != std::string::npos);
    TYPER_CHECK(manifest.find("\"advance_x\":16,\"monospaced\":true", mono)
                < roboto);
    TYPER_CHECK(manifest.find("\"advance_y\":33", mono) < roboto);
    TYPER_CHECK(manifest.find(
        "\"glyph_bounds\":{\"top\":-26,\"bottom\":5}", mono) < roboto);
    TYPER_CHECK(manifest.find(
        "\"unicode_ranges\":[[32,126],[1025,1025],[1040,1103],[1105,1105]]",
        mono) < roboto);
    TYPER_CHECK(manifest.find("\"advance_x\":[", roboto)
                != std::string::npos);
    TYPER_CHECK(manifest.find("\"monospaced\":false", roboto)
                != std::string::npos);
}

}  // namespace

int main(int argc, char **argv) {
    return typer::test::run(argc, argv, {
        {"schema_and_sorting", schema_and_sorting},
        {"metrics_ranges_and_monospacing", metrics_ranges_and_monospacing},
    });
}
