#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

#include "../batch_protocol.h"
#include "test_runner.h"

namespace {

void expect_tokens(std::string_view input, std::vector<std::string> expected) {
    TYPER_CHECK(typer::batch::tokenize(input) == expected);
}

void expect_invalid(std::string_view input) {
    try {
        (void) typer::batch::tokenize(input);
    } catch (const std::invalid_argument &) {
        return;
    }
    typer::test::fail("expected invalid_argument", __FILE__, __LINE__);
}

void empty_and_whitespace() {
    expect_tokens("", {});
    expect_tokens("  \t\r\n", {});
}

void basic_command() {
    expect_tokens("--batch clear -c 000000",
                  {"--batch", "clear", "-c", "000000"});
}

void double_and_single_quotes() {
    expect_tokens("--batch text -t \"Hello world\"",
                  {"--batch", "text", "-t", "Hello world"});
    expect_tokens("--batch text -t 'single quoted'",
                  {"--batch", "text", "-t", "single quoted"});
}

void empty_and_adjacent_quotes() {
    expect_tokens("--batch text -t \"\"",
                  {"--batch", "text", "-t", ""});
    expect_tokens("empty='' adjacent=a\"\"b",
                  {"empty=", "adjacent=ab"});
}

void escaped_characters() {
    expect_tokens(R"(--batch text -t "file \"one\" \\ path")",
                  {"--batch", "text", "-t", "file \"one\" \\ path"});
    expect_tokens("escaped\\ space trailing", {"escaped space", "trailing"});
}

void unicode_and_nul() {
    expect_tokens("--batch text -t Unicode-Привет-世界",
                  {"--batch", "text", "-t", "Unicode-Привет-世界"});
    expect_tokens(std::string("one\0two", 7), {"one", "two"});
}

void malformed_input() {
    expect_invalid("--batch text -t \"unterminated");
    expect_invalid("--batch text -t trailing\\");
}

void command_splitting() {
    TYPER_CHECK(typer::batch::split_commands({}).empty());
    TYPER_CHECK((typer::batch::split_commands(
        {"--batch", "clear", "--batch", "flush"}) ==
        std::vector<std::vector<std::string>>{
            {"--batch", "clear"}, {"--batch", "flush"}}));
    TYPER_CHECK((typer::batch::split_commands({"clear", "--batch", "flush"}) ==
        std::vector<std::vector<std::string>>{
            {"--batch", "clear"}, {"--batch", "flush"}}));
    TYPER_CHECK((typer::batch::split_commands(
        {"--batch", "--batch", "clear", "--batch"}) ==
        std::vector<std::vector<std::string>>{{"--batch", "clear"}}));
}

void composite_button_command() {
    const auto commands = typer::batch::split_commands(typer::batch::tokenize(
        "--batch button -p 25 90 -s 365 125 --background 030607 "
        "--border 35d9e6 --text-color 35d9e6 -f \"JetBrainsMono 16pt\" "
        "-t \"[PRINT FILES]\" --id 7:nav.files"));
    TYPER_CHECK(commands.size() == 1);
    TYPER_CHECK(commands[0][1] == "button");
    TYPER_CHECK(commands[0][15] == "JetBrainsMono 16pt");
    TYPER_CHECK(commands[0].back() == "7:nav.files");

    const auto minus = typer::batch::split_commands(typer::batch::tokenize(
        "--batch button -p 0 0 -s 100 50 -t \" -5\" --id heat.minus"));
    TYPER_CHECK(minus.size() == 1);
    TYPER_CHECK(minus[0][9] == " -5");
}

void feather_protocol_fixture() {
#ifdef FEATHER_PROTOCOL_FIXTURE
    std::ifstream fixture(FEATHER_PROTOCOL_FIXTURE, std::ios::binary);
    TYPER_CHECK(fixture.good());
    std::string frame((std::istreambuf_iterator<char>(fixture)),
                      std::istreambuf_iterator<char>());
    const auto terminator = frame.find("--end");
    TYPER_CHECK(terminator != std::string::npos);
    frame.resize(terminator);
    const auto commands = typer::batch::split_commands(
        typer::batch::tokenize(frame));
    TYPER_CHECK(commands.size() == 7);
    TYPER_CHECK(commands[0] ==
                std::vector<std::string>({"--batch", "clear-hitboxes"}));
    TYPER_CHECK(commands[4].back() == "file \"one\" \\ Привет");
    TYPER_CHECK(commands[5][1] == "hitbox");
    TYPER_CHECK(commands[5][3] == "print.pause");
    TYPER_CHECK(commands[6] ==
                std::vector<std::string>({"--batch", "flush"}));
#endif
}

}  // namespace

int main(int argc, char **argv) {
    return typer::test::run(argc, argv, {
        {"empty_and_whitespace", empty_and_whitespace},
        {"basic_command", basic_command},
        {"double_and_single_quotes", double_and_single_quotes},
        {"empty_and_adjacent_quotes", empty_and_adjacent_quotes},
        {"escaped_characters", escaped_characters},
        {"unicode_and_nul", unicode_and_nul},
        {"malformed_input", malformed_input},
        {"command_splitting", command_splitting},
        {"composite_button_command", composite_button_command},
        {"feather_protocol_fixture", feather_protocol_fixture},
    });
}
