// Screen typer
//
// Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include <algorithm>
#include <csignal>
#include <iostream>
#include <map>
#include <ranges>
#include <string>
#include <string_view>
#include <vector>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

#include "../../lib/argparse/argparse.hpp"

#include "../common/text.h"
#include "interactive.h"
#include "batch_protocol.h"
#include "framebuffer.h"


#include "../common/fonts/JetBrainsMono12ptb2.h"
#include "../common/fonts/JetBrainsMono16ptb2.h"
#include "../common/fonts/JetBrainsMono20ptb2.h"
#include "../common/fonts/JetBrainsMono28ptb2.h"
#include "../common/fonts/JetBrainsMono8ptb4.h"
#include "../common/fonts/JetBrainsMonoBold12ptb2.h"
#include "../common/fonts/JetBrainsMonoBold16ptb2.h"
#include "../common/fonts/JetBrainsMonoBold20ptb2.h"
#include "../common/fonts/JetBrainsMonoBold28ptb2.h"
#include "../common/fonts/JetBrainsMonoBold8ptb4.h"
#include "../common/fonts/JetBrainsMonoThin12ptb4.h"
#include "../common/fonts/JetBrainsMonoThin16ptb2.h"
#include "../common/fonts/JetBrainsMonoThin20ptb2.h"
#include "../common/fonts/JetBrainsMonoThin28ptb2.h"
#include "../common/fonts/JetBrainsMonoThin8ptb4.h"
#include "../common/fonts/Roboto12pt.h"
#include "../common/fonts/Roboto16pt.h"
#include "../common/fonts/Roboto20pt.h"
#include "../common/fonts/Roboto28pt.h"
#include "../common/fonts/Roboto8ptb4.h"
#include "../common/fonts/RobotoBold12pt.h"
#include "../common/fonts/RobotoBold16pt.h"
#include "../common/fonts/RobotoBold20pt.h"
#include "../common/fonts/RobotoBold28pt.h"
#include "../common/fonts/RobotoBold8ptb4.h"
#include "../common/fonts/RobotoThin12ptb4.h"
#include "../common/fonts/RobotoThin16ptb2.h"
#include "../common/fonts/RobotoThin20ptb2.h"
#include "../common/fonts/RobotoThin28ptb2.h"
#include "../common/fonts/RobotoThin8ptb4.h"
#define WIDTH 800
#define HEIGHT 480

bool DEBUG = false;

std::map<std::string, const Font *> fonts{
    {Roboto8ptb4.name, &Roboto8ptb4},
    {Roboto12pt.name, &Roboto12pt},
    {Roboto16pt.name, &Roboto16pt},
    {Roboto20pt.name, &Roboto20pt},
    {Roboto28pt.name, &Roboto28pt},
    {RobotoBold8ptb4.name, &RobotoBold8ptb4},
    {RobotoBold12pt.name, &RobotoBold12pt},
    {RobotoBold16pt.name, &RobotoBold16pt},
    {RobotoBold20pt.name, &RobotoBold20pt},
    {RobotoBold28pt.name, &RobotoBold28pt},
    {RobotoThin8ptb4.name, &RobotoThin8ptb4},
    {RobotoThin12ptb4.name, &RobotoThin12ptb4},
    {RobotoThin16ptb2.name, &RobotoThin16ptb2},
    {RobotoThin20ptb2.name, &RobotoThin20ptb2},
    {RobotoThin28ptb2.name, &RobotoThin28ptb2},

    {JetBrainsMono8ptb4.name, &JetBrainsMono8ptb4},
    {JetBrainsMono12ptb2.name, &JetBrainsMono12ptb2},
    {JetBrainsMono16ptb2.name, &JetBrainsMono16ptb2},
    {JetBrainsMono20ptb2.name, &JetBrainsMono20ptb2},
    {JetBrainsMono28ptb2.name, &JetBrainsMono28ptb2},
    {JetBrainsMonoBold8ptb4.name, &JetBrainsMonoBold8ptb4},
    {JetBrainsMonoBold12ptb2.name, &JetBrainsMonoBold12ptb2},
    {JetBrainsMonoBold16ptb2.name, &JetBrainsMonoBold16ptb2},
    {JetBrainsMonoBold20ptb2.name, &JetBrainsMonoBold20ptb2},
    {JetBrainsMonoBold28ptb2.name, &JetBrainsMonoBold28ptb2},
    {JetBrainsMonoThin8ptb4.name, &JetBrainsMonoThin8ptb4},
    {JetBrainsMonoThin12ptb4.name, &JetBrainsMonoThin12ptb4},
    {JetBrainsMonoThin16ptb2.name, &JetBrainsMonoThin16ptb2},
    {JetBrainsMonoThin20ptb2.name, &JetBrainsMonoThin20ptb2},
    {JetBrainsMonoThin28ptb2.name, &JetBrainsMonoThin28ptb2},
};

void drawText(const argparse::ArgumentParser &opts, TextDrawer &drawer) {
    auto pos = opts.get<std::vector<int>>("--pos");
    auto color = opts.get<uint32_t>("--color");
    auto bgColor = opts.get<uint32_t>("--bg-color");
    auto text = opts.get<std::string>("--text");
    auto scale = (uint8_t) opts.get<int>("--scale");
    auto fontName = opts.get("--font");
    auto hAlignStr = opts.get("--h-align");
    auto vAlignVStr = opts.get("--v-align");
    auto maxWidth = opts.get<int>("--max-width");
    auto maxHeight = opts.get<int>("--max-height");
    auto wrap = opts.get<bool>("--wrap");
    auto truncate = opts.get<bool>("--truncate");

    if ((wrap || truncate) && maxWidth <= 0) {
        throw std::invalid_argument("--wrap and --truncate require --max-width");
    }
    if (wrap && maxHeight <= 0) {
        throw std::invalid_argument("--wrap requires --max-height");
    }

    HorizontalAlign hAlign;
    if (hAlignStr == "center") {
        hAlign = HorizontalAlign::CENTER;
    } else if (hAlignStr == "right") {
        hAlign = HorizontalAlign::RIGHT;
    } else {
        hAlign = HorizontalAlign::LEFT;
    }

    VerticalAlignment vAlign;
    if (vAlignVStr == "bottom") {
        vAlign = VerticalAlignment::BOTTOM;
    } else if (vAlignVStr == "baseline") {
        vAlign = VerticalAlignment::BASELINE;
    } else if (vAlignVStr == "middle") {
        vAlign = VerticalAlignment::MIDDLE;
    } else {
        vAlign = VerticalAlignment::TOP;
    }


    if (!fonts.contains(fontName)) {
        throw std::invalid_argument("Unknown font name: " + fontName);
    }

    const Font *font = fonts[fontName];

    if (pos.size() == 2) drawer.setPosition(pos[0], pos[1]);
    drawer.setColor(color | 0xff000000);
    drawer.setBackgroundColor(opts.is_used("--bg-color") ? 0xff000000 | bgColor : 0);
    drawer.setFont(font);
    drawer.setFontScale(scale, scale);
    drawer.setHorizontalAlignment(hAlign);
    drawer.setVerticalAlignment(vAlign);

    if (wrap) {
        drawer.printWrapped(text.c_str(), maxWidth, maxHeight, truncate);
    } else if (truncate) {
        drawer.printTruncated(text.c_str(), maxWidth);
    } else {
        drawer.print(text.c_str());
    }
}

void fill(const argparse::ArgumentParser &opts, TextDrawer &drawer) {
    auto pos = opts.get<std::vector<int>>("--pos");
    auto size = opts.get<std::vector<int>>("--size");
    auto color = 0xff000000 | opts.get<uint32_t>("--color");

    drawer.fillRect(pos[0], pos[1], size[0], size[1], color);
}

StrokeDirection _parseDirection(const std::string &value) {
    if (value == "outer") return StrokeDirection::OUTER;
    if (value == "middle") return StrokeDirection::MIDDLE;
    if (value == "inner") return StrokeDirection::INNER;

    throw std::invalid_argument("Invalid value for StrokeDirection: " + value);
}

void stroke(const argparse::ArgumentParser &opts, TextDrawer &drawer) {
    auto pos = opts.get<std::vector<int>>("--pos");
    auto size = opts.get<std::vector<int>>("--size");
    auto color = 0xff000000 | opts.get<uint32_t>("--color");
    auto lineWidth = std::max<uint8_t>(1, opts.get<uint8_t>("--line-width"));
    auto strokeDirection = opts.get("--stroke-direction");

    drawer.setStrokeDirection(_parseDirection(strokeDirection));
    drawer.strokeRect(pos[0], pos[1], size[0], size[1], color, lineWidth);
}

void line(const argparse::ArgumentParser &opts, TextDrawer &drawer) {
    auto start = opts.get<std::vector<int>>("--start");
    auto end = opts.get<std::vector<int>>("--end");
    auto color = 0xff000000 | opts.get<uint32_t>("--color");
    auto lineWidth = std::max<uint8_t>(1, opts.get<uint8_t>("--line-width"));

    drawer.drawLine(start[0], start[1], end[0], end[1], color, lineWidth);
}

void clear(const argparse::ArgumentParser &opts, TextDrawer &drawer) {
    auto color = 0xff000000 | opts.get<uint32_t>("--color");
    drawer.clear(color);
}

void add_hitbox(const argparse::ArgumentParser &opts) {
    auto pos = opts.get<std::vector<int>>("--pos");
    auto size = opts.get<std::vector<int>>("--size");
    auto action = opts.get<std::string>("--id");
    auto valid_char = [](unsigned char ch) {
        return (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z')
            || (ch >= '0' && ch <= '9') || ch == '_' || ch == '.'
            || ch == ':' || ch == '-';
    };
    if (action.empty() || action.size() > 64
        || !std::all_of(action.begin(), action.end(), valid_char)) {
        throw std::invalid_argument("Hitbox id must be 1-64 ASCII identifier characters");
    }
    if (size[0] <= 0 || size[1] <= 0) {
        throw std::invalid_argument("Hitbox size must be positive");
    }

    typer::interactive::register_hitbox(
        pos[0], pos[1], size[0], size[1], std::move(action),
        opts.get<bool>("--continuous"));
}

void drawButton(const argparse::ArgumentParser &opts, TextDrawer &drawer) {
    const auto pos = opts.get<std::vector<int>>("--pos");
    const auto size = opts.get<std::vector<int>>("--size");
    const auto background = 0xff000000 | opts.get<uint32_t>("--background");
    const auto border = 0xff000000 | opts.get<uint32_t>("--border");
    const auto textColor = 0xff000000 | opts.get<uint32_t>("--text-color");
    const auto lineWidth = std::max<uint8_t>(1, opts.get<uint8_t>("--line-width"));
    const auto fontName = opts.get("--font");
    const auto label = opts.get<std::string>("--text");
    const auto maxWidth = opts.get<int>("--max-width");
    const auto truncate = opts.get<bool>("--truncate");

    if (truncate && maxWidth <= 0) {
        throw std::invalid_argument("button --truncate requires --max-width");
    }

    if (!fonts.contains(fontName)) {
        throw std::invalid_argument("Unknown font name: " + fontName);
    }
    drawer.fillRect(pos[0], pos[1], size[0], size[1], background);
    drawer.setStrokeDirection(StrokeDirection::INNER);
    drawer.strokeRect(pos[0], pos[1], size[0], size[1], border, lineWidth);
    drawer.setPosition(pos[0] + size[0] / 2, pos[1] + size[1] / 2);
    drawer.setColor(textColor);
    drawer.setBackgroundColor(0);
    drawer.setFont(fonts[fontName]);
    drawer.setFontScale(1, 1);
    drawer.setHorizontalAlignment(HorizontalAlign::CENTER);
    drawer.setVerticalAlignment(VerticalAlignment::MIDDLE);
    if (truncate) {
        drawer.printTruncated(label.c_str(), maxWidth);
    } else {
        drawer.print(label.c_str());
    }

    if (opts.is_used("--id")) add_hitbox(opts);
}

void clear_hitboxes() {
    typer::interactive::clear_hitboxes();
}

std::vector<std::vector<std::string>> parse_batch_args(const std::vector<std::string> &args) {
    return typer::batch::split_commands(args);
}

struct ProgramParser {
    argparse::ArgumentParser program;
    argparse::ArgumentParser batch_parser;
    argparse::ArgumentParser text_command;
    argparse::ArgumentParser fill_command;
    argparse::ArgumentParser stroke_command;
    argparse::ArgumentParser line_command;
    argparse::ArgumentParser clear_command;
    argparse::ArgumentParser flush_command;
    argparse::ArgumentParser hitbox_command;
    argparse::ArgumentParser clear_hitboxes_command;
    argparse::ArgumentParser button_command;
};

std::unique_ptr<ProgramParser> build_parser(argparse::default_arguments def = argparse::default_arguments::all) {
    auto result = std::unique_ptr<ProgramParser>( // NOLINT(*-make-unique)
        new ProgramParser{
            .program = argparse::ArgumentParser("typer", "1.0", def),
            .batch_parser = argparse::ArgumentParser("batch"),
            .text_command = argparse::ArgumentParser("text"),
            .fill_command = argparse::ArgumentParser("fill"),
            .stroke_command = argparse::ArgumentParser("stroke"),
            .line_command = argparse::ArgumentParser("line"),
            .clear_command = argparse::ArgumentParser("clear"),
            .flush_command = argparse::ArgumentParser("flush"),
            .hitbox_command = argparse::ArgumentParser("hitbox"),
            .clear_hitboxes_command = argparse::ArgumentParser("clear-hitboxes"),
            .button_command = argparse::ArgumentParser("button"),
        }
    );

    result->program.add_description("Flashforge AD5M screen drawing utility");
    result->program.add_epilog("Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>");

    result->program.add_argument("--debug").flag();
    result->program.add_argument("--double-buffered", "-db").flag();
    result->program.add_argument("--list-fonts").help("List loaded fonts and exit.").flag();
    result->program.add_argument("--touch-device")
        .default_value("")
        .help("Read normalized Linux input events from this device.");
    result->program.add_argument("--event-pipe")
        .default_value("")
        .help("Write touch action events to this named pipe.");

    // ************ Batch Parser

    result->batch_parser.add_description("Batch processing");
    result->batch_parser.add_epilog("Example: ./typer batch \\\n"
        "    --batch clear <...> \\\n"
        "    --batch fill <...> \\\n"
        "    --batch text <...>");

    result->batch_parser.add_argument("--pipe")
        .default_value("")
        .help("A pipe with batches to read");

    result->batch_parser.add_argument("--batch")
        .help("Beginning of a batch")
        .remaining();

    result->program.add_subparser(result->batch_parser);

    // ************ Text Parser


    result->text_command.add_description("Prints text at position");

    result->text_command.add_argument("--pos", "-p")
        .nargs(2)
        .scan<'d', int>();

    result->text_command.add_argument("--color", "-c")
        .scan<'X', uint32_t>()
        .default_value(0xffffffff);

    result->text_command.add_argument("--bg-color", "-b")
        .scan<'X', uint32_t>()
        .default_value(0u);

    result->text_command.add_argument("--font", "-f")
        .default_value(Roboto12pt.name);

    result->text_command.add_argument("--scale", "-s")
        .scan<'d', int>()
        .default_value(1);

    result->text_command.add_argument("--text", "-t")
        .default_value("");

    result->text_command.add_argument("--h-align", "-ha")
        .choices("left", "center", "right")
        .default_value("left");

    result->text_command.add_argument("--v-align", "-va")
        .choices("bottom", "baseline", "middle", "top")
        .default_value("baseline");

    result->text_command.add_argument("--max-width")
        .scan<'d', int>()
        .default_value(0)
        .help("Maximum rendered width in pixels");

    result->text_command.add_argument("--max-height")
        .scan<'d', int>()
        .default_value(0)
        .help("Maximum rendered height in pixels");

    result->text_command.add_argument("--wrap")
        .help("Wrap text within max-width and max-height")
        .flag();

    result->text_command.add_argument("--truncate")
        .help("Truncate text to max-width and append an ellipsis")
        .flag();

    result->program.add_subparser(result->text_command);

    // ************ Fill Parser

    result->fill_command.add_description("Fill specified region with color");
    result->fill_command.add_argument("--pos", "-p")
        .nargs(2)
        .scan<'d', int>()
        .required();

    result->fill_command.add_argument("--size", "-s")
        .nargs(2)
        .scan<'d', int>()
        .required();

    result->fill_command.add_argument("--color", "-c")
        .scan<'X', uint32_t>()
        .default_value(0u);

    result->program.add_subparser(result->fill_command);

    // ************ Stroke Parser

    result->stroke_command.add_description("Stroke specified region with color");
    result->stroke_command.add_argument("--pos", "-p")
        .nargs(2)
        .scan<'d', int>()
        .required();

    result->stroke_command.add_argument("--size", "-s")
        .nargs(2)
        .scan<'d', int>()
        .required();

    result->stroke_command.add_argument("--color", "-c")
        .scan<'X', uint32_t>()
        .default_value(0xffffffu);

    result->stroke_command.add_argument("--line-width", "-lw")
        .scan<'d', uint8_t>()
        .default_value((uint8_t) 1);

    result->stroke_command.add_argument("--stroke-direction", "-sd")
        .choices("outer", "middle", "inner")
        .default_value("middle");

    result->program.add_subparser(result->stroke_command);

    // ************ Line Parser

    result->line_command.add_description("Draw line with color");
    result->line_command.add_argument("--start", "-s")
        .nargs(2)
        .scan<'d', int>()
        .required();

    result->line_command.add_argument("--end", "-e")
        .nargs(2)
        .scan<'d', int>()
        .required();

    result->line_command.add_argument("--color", "-c")
        .scan<'X', uint32_t>()
        .default_value(0xffffffu);

    result->line_command.add_argument("--line-width", "-lw")
        .scan<'d', uint8_t>()
        .default_value((uint8_t) 1);

    result->program.add_subparser(result->line_command);

    // ************ Clear Parser

    result->clear_command.add_description("Clear entire screen");

    result->clear_command.add_argument("--color", "-c")
        .scan<'X', uint32_t>()
        .default_value(0u);

    result->program.add_subparser(result->clear_command);

    // ************ Flush Parser
    result->flush_command.add_description("Flush pending changes (for --double-buffered mode)");
    result->program.add_subparser(result->flush_command);

    // ************ Interactive display list

    result->hitbox_command.add_description("Register a touch hitbox for the current screen");
    result->hitbox_command.add_argument("--pos", "-p")
        .nargs(2)
        .scan<'d', int>()
        .required();
    result->hitbox_command.add_argument("--size", "-s")
        .nargs(2)
        .scan<'d', int>()
        .required();
    result->hitbox_command.add_argument("--id")
        .required();

    result->hitbox_command.add_argument("--continuous")
        .help("Emit begin/move/end coordinates while the control is held.")
        .flag();
    result->program.add_subparser(result->hitbox_command);

    result->clear_hitboxes_command.add_description("Remove all registered touch hitboxes");
    result->program.add_subparser(result->clear_hitboxes_command);

    // ************ Composite Feather button
    result->button_command.add_description("Draw and optionally register a button");
    result->button_command.add_argument("--pos", "-p").nargs(2).scan<'d', int>().required();
    result->button_command.add_argument("--size", "-s").nargs(2).scan<'d', int>().required();
    result->button_command.add_argument("--background").scan<'X', uint32_t>().default_value(0u);
    result->button_command.add_argument("--border").scan<'X', uint32_t>().default_value(0xffffffu);
    result->button_command.add_argument("--text-color").scan<'X', uint32_t>().default_value(0xffffffu);
    result->button_command.add_argument("--line-width", "-lw").scan<'d', uint8_t>().default_value((uint8_t) 2);
    result->button_command.add_argument("--font", "-f").default_value(Roboto12pt.name);
    result->button_command.add_argument("--text", "-t").default_value("");
    result->button_command.add_argument("--max-width").scan<'d', int>().default_value(0);
    result->button_command.add_argument("--truncate").flag();
    result->button_command.add_argument("--id");
    result->button_command.add_argument("--continuous").flag();
    result->program.add_subparser(result->button_command);

    return result;
}

void run_program(const ProgramParser &args, TextDrawer &drawer) {
    auto &[
        program, batch_parser, text_command, fill_command,
        stroke_command, line_command, clear_command, flush_command,
        hitbox_command, clear_hitboxes_command, button_command
    ] = args;

    if (program.is_subcommand_used("fill")) {
        fill(fill_command, drawer);
    } else if (program.is_subcommand_used("text")) {
        drawText(text_command, drawer);
    } else if (program.is_subcommand_used("stroke")) {
        stroke(stroke_command, drawer);
    } else if (program.is_subcommand_used("line")) {
        line(line_command, drawer);
    } else if (program.is_subcommand_used("clear")) {
        clear(clear_command, drawer);
    } else if (program.is_subcommand_used("flush")) {
        drawer.flush();
    } else if (program.is_subcommand_used("hitbox")) {
        add_hitbox(hitbox_command);
    } else if (program.is_subcommand_used("clear-hitboxes")) {
        clear_hitboxes();
    } else if (program.is_subcommand_used("button")) {
        drawButton(button_command, drawer);
    } else {
        std::cerr << "Unknown program: " << program << std::endl;
    }
}

void process_batch(TextDrawer &drawer, const std::vector<std::string> &batch) {
    auto batch_parser = build_parser(argparse::default_arguments::none);

    try {
        batch_parser->program.parse_args(batch);
        run_program(*batch_parser, drawer);
    } catch (const std::exception &e) {
        std::cerr << "Unable to process batch: ";
        for (const auto &str: batch) { std::cerr << "\"" << str << "\" "; }
        std::cerr << std::endl;
        std::cerr << "Error: " << e.what() << std::endl;
    }
}

void process_draw_frame(TextDrawer &drawer, std::string_view frame) {
    auto tokens = typer::batch::tokenize(frame);
    auto batches = parse_batch_args(tokens);
    for (const auto &batch: batches) process_batch(drawer, batch);
}

void process_pipe_batches(TextDrawer &drawer, const std::string &pipe,
                          const std::string &touch_device, const std::string &event_pipe) {
    typer::interactive::run(
        pipe, touch_device, event_pipe,
        [&drawer](std::string_view frame) { process_draw_frame(drawer, frame); },
        DEBUG);
}


int main(int argc, char *argv[]) {
    auto main = build_parser();

    try {
        main->program.parse_args(argc, argv);
    } catch (const std::exception &e) {
        std::cerr << "Unable to parse args: " << e.what() << std::endl;

        std::cout << main->program << std::endl;
        return 1;
    }

    if (main->program.get<bool>("--list-fonts")) {
        std::cout << "Loaded fonts: " << std::endl;

        const auto keys = std::ranges::views::keys(fonts);
        for (const auto &key: keys) {
            std::cout << "- " << key << std::endl;
        }

        return 1;
    }

#ifdef __linux__
    // Interactive typer belongs to the Klippy process that launched it. Avoid
    // retaining the framebuffer and backbuffer if Klippy is terminated before
    // it can run the normal renderer shutdown path.
    if (main->program.is_subcommand_used("batch") &&
        !main->batch_parser.get("--pipe").empty()) {
        auto parent = getppid();
        if (prctl(PR_SET_PDEATHSIG, SIGTERM) == -1) {
            std::cerr << "Warning: unable to configure parent-death signal" << std::endl;
        } else if (getppid() != parent) {
            return 0;
        }
    }
#endif

    const bool double_buffered = main->program.get<bool>("--double-buffered");
    typer::framebuffer::Device framebuffer(
        "/dev/fb0", WIDTH, HEIGHT, double_buffered);
    if (!framebuffer.valid()) {
        std::cerr << "Error: " << framebuffer.error() << std::endl;
        return 1;
    }
    if (!framebuffer.warning().empty()) {
        std::cerr << "Warning: " << framebuffer.warning() << std::endl;
    }

    TextDrawer drawer(framebuffer.front(), WIDTH, HEIGHT);
    drawer.setDoubleBuffered(double_buffered, framebuffer.back());

    DEBUG = main->program.get<bool>("--debug");
    drawer.setDebug(DEBUG);

    if (main->program.is_subcommand_used("batch")) {
        auto pipe = main->batch_parser.get("--pipe");

        if (pipe.empty()) {
            auto batch_args = main->batch_parser.get<std::vector<std::string>>("--batch");
            auto batches = parse_batch_args(batch_args);

            for (auto &batch: batches) {
                process_batch(drawer, batch);
            }
        } else {
            process_pipe_batches(
                drawer, pipe,
                main->program.get("--touch-device"),
                main->program.get("--event-pipe")
            );
        }
    } else {
        run_program(*main, drawer);
    }

    drawer.flush();

    return 0;
}
