## One /proc sampling pass for the printer resource monitor.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

## Prints the complete TSV block for one instant: the system row, then one row
## per process the caller has already classified.  The caller passes that list
## in FF5M_PROCESSES as "pid<TAB>role<TAB>command" lines, the sampled wall
## clock in epoch, and the matching /proc/uptime value in uptime.  root points
## at the /proc to read and exists so tests can run this pass against a
## fixture tree.
##
## Every file this sampler needs is read in this one pass on purpose.  The
## shell loop used to fork date, five awks for the system row and two more awks
## for each matched process every second.  On the printer's dual-core A7 those
## forks competed with Klipper's reactor, which means the sampler was adding to
## the very host overload it exists to observe.  Keep new work inside this pass
## rather than adding another command substitution to the loop.

function read_line(path,    line, status) {
    line = ""
    status = (getline line < path)
    close(path)
    return (status > 0) ? line : ""
}

## Store parts[2] for every line of `path` whose first word is a key of
## `wanted`.  Covers meminfo, status and io, which all use "Key: value" lines.
function read_keyed(path, wanted, out,    line, parts, count) {
    while ((getline line < path) > 0) {
        count = split(line, parts, /[ \t:]+/)
        if (count >= 2 && (parts[1] in wanted))
            out[parts[1]] = parts[2] + 0
    }
    close(path)
}

function stat_field(values, count, position) {
    return (count >= position) ? values[position] + 0 : 0
}

BEGIN {
    if (root == "") root = "/proc"

    # Formats live in variables because both are too wide for one line and awk
    # string continuation inside a printf argument list is easy to misread.
    # %.0f rather than %d: counters such as total CPU ticks and read_bytes
    # outgrow a 32-bit integer on a long-running printer, and awk's default
    # number formatting would switch them to exponent notation.
    system_format = "%s\t%s\t%s\t%.0f\t%.0f\tsystem\t0\t%.0f\t0\t-"
    system_format = system_format "\t0\t0\t0\t0\t0\t0\t0\t0"
    system_format = system_format "\t0\t0\t0\t0\t-\t-\n"
    process_format = "%s\t%s\t%s\t%.0f\t%.0f\t%s\t%s\t%.0f\t%.0f\t%s"
    process_format = process_format "\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f"
    process_format = process_format "\t%.0f\t%.0f\t%.0f\t%.0f\t%.0f"
    process_format = process_format "\t%.0f\t%.0f\t%s\t%s\n"

    load1 = 0
    if (split(read_line(root "/loadavg"), parts, /[ \t]+/) >= 1)
        if (parts[1] != "") load1 = parts[1]

    memory_wanted["MemAvailable"] = 1
    memory_wanted["SwapFree"] = 1
    memory["MemAvailable"] = 0
    memory["SwapFree"] = 0
    read_keyed(root "/meminfo", memory_wanted, memory)

    total_ticks = 0
    while ((getline line < (root "/stat")) > 0) {
        if (line ~ /^cpu[ \t]/) {
            count = split(line, parts, /[ \t]+/)
            for (position = 2; position <= count; position++)
                total_ticks += parts[position]
            break
        }
    }
    close(root "/stat")

    printf system_format, epoch, uptime, load1, memory["MemAvailable"],
        memory["SwapFree"], total_ticks

    status_wanted["VmRSS"] = 1
    status_wanted["voluntary_ctxt_switches"] = 1
    status_wanted["nonvoluntary_ctxt_switches"] = 1
    io_wanted["read_bytes"] = 1
    io_wanted["write_bytes"] = 1
    io_wanted["syscr"] = 1
    io_wanted["syscw"] = 1

    rows = split(ENVIRON["FF5M_PROCESSES"], process_lines, "\n")
    for (row = 1; row <= rows; row++) {
        if (process_lines[row] == "") continue
        split(process_lines[row], item, "\t")
        pid = item[1]
        role = item[2]
        command = (item[3] == "") ? "-" : item[3]
        base = root "/" pid

        # /proc/<pid>/stat holds comm in parentheses and comm may contain
        # spaces, so fields are counted from after the closing bracket. The
        # offsets below therefore match /proc's field numbers minus two.
        statline = read_line(base "/stat")
        cut = index(statline, ") ")
        if (cut == 0) continue
        count = split(substr(statline, cut + 2), fields, /[ \t]+/)
        state = (count >= 1 && fields[1] != "") ? fields[1] : "-"
        minor_faults = stat_field(fields, count, 8)
        major_faults = stat_field(fields, count, 10)
        cpu_ticks = stat_field(fields, count, 12) + stat_field(fields, count, 13)
        threads = stat_field(fields, count, 18)

        status["VmRSS"] = 0
        status["voluntary_ctxt_switches"] = 0
        status["nonvoluntary_ctxt_switches"] = 0
        read_keyed(base "/status", status_wanted, status)

        sched_runtime = 0
        sched_wait = 0
        sched_timeslices = 0
        if (split(read_line(base "/schedstat"), parts, /[ \t]+/) >= 3) {
            sched_runtime = parts[1] + 0
            sched_wait = parts[2] + 0
            sched_timeslices = parts[3] + 0
        }

        io["read_bytes"] = 0
        io["write_bytes"] = 0
        io["syscr"] = 0
        io["syscw"] = 0
        read_keyed(base "/io", io_wanted, io)

        wchan = read_line(base "/wchan")
        if (wchan == "") wchan = "-"

        printf process_format, epoch, uptime, load1,
            memory["MemAvailable"], memory["SwapFree"],
            role, pid, cpu_ticks, status["VmRSS"], state, threads,
            status["voluntary_ctxt_switches"],
            status["nonvoluntary_ctxt_switches"],
            minor_faults, major_faults, sched_runtime, sched_wait,
            sched_timeslices, io["read_bytes"], io["write_bytes"],
            io["syscr"], io["syscw"], wchan, command
    }
}
