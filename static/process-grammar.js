/*
 * process-grammar.js — the rules a process map has to obey, in one place.
 *
 * The same grammar was written three times over: once in Python
 * (tools/blueprint_converter.py), once for the canvas (static/flow-canvas.html)
 * and once for the builder (static/blueprint-builder.html). Three copies of one
 * standard drift, and the first symptom is a tool calling a map clean that the
 * validator rejects. This is the single JavaScript copy; both browser tools
 * load it from the same directory, exactly as static/spicy-lab.html loads
 * option-lab.js, and tests/test_process_grammar.py holds it against the Python
 * validator case for case.
 *
 * The rules themselves are in .claude/skills/process-mapping/SKILL.md.
 *
 * A map reaches here as one canonical graph, whatever it was drawn in:
 *
 *   { steps: [{ id, number, depth, title, kind, executor, duration,
 *               frequency, gotoTarget, synthetic }],
 *     links: [{ from, to, label, explicit }] }
 *
 *   depth     how far along the flow the step sits — its step number in a
 *             blueprint, its layer on a canvas. "More than three steps back"
 *             is measured in it, so each adapter decides what a step of
 *             distance means in its own representation.
 *   explicit  true when the link is a branch somebody labelled, false when it
 *             is the flow simply falling through to the next step.
 *   a link may point at an id that does not exist; that is a dangling branch,
 *             and reporting it is the point rather than something to guard.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ProcessGrammar = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const STEP_KINDS = ["task", "decision", "delay", "end", "goto"];
  const EXECUTORS = ["person", "automation", "ai"];
  const KIND_LABEL = { task: "Task", decision: "Decision", delay: "Wait", end: "End", goto: "Go to" };
  const EXECUTOR_LABEL = { person: "Person", automation: "Automation", ai: "AI agent" };

  // A loop-back further than this should be a go-to step, not a wire dragged
  // back across the map.
  const LOOP_BACK_LIMIT = 3;

  // A wait, a terminator and a jump are all real parts of a map, and none of
  // them is work anybody sits through.
  function isWork(step) {
    const kind = step.kind || "task";
    return kind !== "delay" && kind !== "end" && kind !== "goto";
  }

  // ---------------------------------------------------------------- duration
  //
  // Every shape a map actually carries one: "45m", "90", "1h30m", "1.5h",
  // "2d", and the prose a blueprint is written in — "30 minutes", "1 hour",
  // "2-8 hours" (read as its midpoint), "instant". Anything unreadable returns
  // null, which leaves the step unpriced rather than quietly worth zero. A day
  // of work is eight hours, not twenty-four.
  const DUR_UNITS = {
    m: 1, min: 1, mins: 1, minute: 1, minutes: 1,
    h: 60, hr: 60, hrs: 60, hour: 60, hours: 60,
    d: 480, day: 480, days: 480,
  };

  function parseDuration(value) {
    if (value === null || value === undefined) return null;
    let text = String(value).trim().toLowerCase();
    if (!text) return null;
    if (text === "instant" || text === "immediate") return 0;
    text = text.replace(/\s+to\s+/g, "-");
    if (/^\d+(\.\d+)?$/.test(text)) return parseFloat(text);

    const range = text.match(/^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*([a-z]+)$/);
    if (range) {
      const unit = DUR_UNITS[range[3]];
      return unit === undefined ? null : ((parseFloat(range[1]) + parseFloat(range[2])) / 2) * unit;
    }

    const token = /(\d+(?:\.\d+)?)\s*([a-z]+)/g;
    let match, total = 0, hit = false;
    while ((match = token.exec(text))) {
      const unit = DUR_UNITS[match[2]];
      if (unit === undefined) return null;
      hit = true;
      total += parseFloat(match[1]) * unit;
    }
    // whatever the numbers did not account for has to be punctuation, or this
    // is prose we should not pretend to have understood
    const rest = text.replace(/(\d+(?:\.\d+)?)\s*([a-z]+)/g, "").replace(/[\s,;.]+/g, "");
    return hit && !rest ? total : null;
  }

  // ---------------------------------------------------------------- adapters

  // A business blueprint's process (docs/blueprint-schema.json). Steps are
  // numbered and flow falls through to the next number unless a decision's
  // branches say otherwise.
  function fromProcess(proc) {
    const source = ((proc && proc.steps) || []).slice().sort(function (a, b) {
      return (a.number || 0) - (b.number || 0);
    });
    const steps = [], links = [];
    const idOf = {};
    source.forEach(function (st, i) { idOf[st.number === undefined ? i + 1 : st.number] = "s" + (st.number === undefined ? i + 1 : st.number); });

    source.forEach(function (st, i) {
      const number = st.number === undefined ? i + 1 : st.number;
      const step = {
        id: idOf[number],
        number: number,
        depth: typeof number === "number" ? number : i + 1,
        title: st.title || "",
        kind: st.kind || "task",
        executor: st.executor || "person",
        duration: st.duration || "",
        frequency: typeof st.frequency === "number" ? st.frequency : null,
      };
      if (step.kind === "goto") {
        step.gotoTarget = st.goto === null || st.goto === undefined
          ? null
          : (idOf[st.goto] || "missing:" + st.goto);
      }
      steps.push(step);
    });

    source.forEach(function (st, i) {
      const number = st.number === undefined ? i + 1 : st.number;
      const from = idOf[number];
      const kind = st.kind || "task";
      if (kind === "goto" || kind === "end") return;  // both end their branch
      const branches = st.branches || [];
      if (!branches.length) {
        const next = source[i + 1];
        if (next) {
          links.push({ from: from, to: idOf[next.number === undefined ? i + 2 : next.number], label: "", explicit: false });
        }
        return;
      }
      branches.forEach(function (branch, bi) {
        const label = branch.label || "";
        if (branch.to === "end") {
          // a branch that stops gets a terminator, so the fork has a visible
          // second way out rather than an arrow into nothing
          const endId = from + "-end" + bi;
          steps.push({
            id: endId, number: null, depth: (typeof number === "number" ? number : i + 1) + 0.5,
            title: "End — " + (label || "terminal"), kind: "end", executor: "person",
            duration: "", frequency: null, synthetic: true,
          });
          links.push({ from: from, to: endId, label: label, explicit: true });
          return;
        }
        const to = branch.to === null || branch.to === undefined
          ? "missing:none"
          : (idOf[branch.to] || "missing:" + branch.to);
        links.push({ from: from, to: to, label: label, explicit: true });
      });
    });

    return { steps: steps, links: links };
  }

  // A flow-canvas document. Cards and wires rather than numbers, so how far
  // back a wire reaches is measured in layers.
  const CANVAS_EXECUTOR = { person: "person", auto: "automation", ai: "ai" };

  function fromCanvas(state) {
    const nodes = (state && state.nodes) || [];
    const edges = (state && state.links) || (state && state.edges) || [];
    const decision = {};
    const steps = nodes.map(function (n) {
      const kind = n.kind === "goto" || n.kind === "delay" || n.kind === "end"
        ? n.kind
        : (n.decision ? "decision" : "task");
      decision[n.id] = kind === "decision";
      const step = {
        id: n.id,
        number: null,
        depth: 0,
        title: n.title || "",
        kind: kind,
        executor: CANVAS_EXECUTOR[n.owner] || "person",
        duration: n.dur || "",
        frequency: typeof n.freq === "number" ? n.freq : null,
      };
      if (kind === "goto") step.gotoTarget = n.target || null;
      return step;
    });
    const links = edges.map(function (e) {
      return { from: e.from, to: e.to, label: e.label || "", explicit: !!decision[e.from] };
    });
    const graph = { steps: steps, links: links };
    const layer = ranks(graph);
    steps.forEach(function (st) { st.depth = (layer[st.id] || 0) + 1; });
    return graph;
  }

  // ------------------------------------------------------------------ layout

  // Longest-path layer per step, over the forward edges only.
  //
  // A loop-back must not drag the step it returns to down the map with it: if
  // it did, the wire that closes the loop would measure as a step forward and
  // some innocent wire near the start would measure as the long jump. So the
  // back edges are classified with a depth-first walk and left out of the
  // layering — which is what makes "more than three steps back" mean what it
  // says on a map that loops.
  function ranks(graph) {
    const steps = graph.steps || [], links = graph.links || [];
    const outgoing = {}, state = {}, back = {};
    steps.forEach(function (st) { outgoing[st.id] = []; state[st.id] = 0; });
    links.forEach(function (l) {
      if (outgoing[l.from] && state[l.to] !== undefined) outgoing[l.from].push(l.to);
    });
    function key(a, b) { return a + "\u0000" + b; }

    // 0 unvisited, 1 on the stack, 2 done — an edge into 1 closes a loop
    steps.forEach(function (root) {
      if (state[root.id] !== 0) return;
      state[root.id] = 1;
      const stack = [{ id: root.id, at: 0 }];
      while (stack.length) {
        const top = stack[stack.length - 1];
        const kids = outgoing[top.id];
        if (top.at >= kids.length) { state[top.id] = 2; stack.pop(); continue; }
        const next = kids[top.at++];
        if (state[next] === 1) { back[key(top.id, next)] = true; continue; }
        if (state[next] === 0) { state[next] = 1; stack.push({ id: next, at: 0 }); }
      }
    });

    const forward = links.filter(function (l) {
      return outgoing[l.from] && state[l.to] !== undefined && !back[key(l.from, l.to)];
    });
    const rank = {}, indegree = {};
    steps.forEach(function (st) { rank[st.id] = 0; indegree[st.id] = 0; });
    forward.forEach(function (l) { indegree[l.to]++; });
    const queue = steps.filter(function (st) { return indegree[st.id] === 0; })
                       .map(function (st) { return st.id; });
    let guard = 0;
    while (queue.length && guard++ < 100000) {
      const id = queue.shift();
      forward.forEach(function (l) {
        if (l.from !== id) return;
        if (rank[l.to] < rank[id] + 1) rank[l.to] = rank[id] + 1;
        if (--indegree[l.to] === 0) queue.push(l.to);
      });
    }
    return rank;
  }

  // ----------------------------------------------------------------- costing

  // Person steps are the labour bill. A step missing either number is unpriced
  // rather than free, and says so instead of dragging the total down silently.
  function monthlyLoad(graph) {
    let minutes = 0, unpriced = 0;
    (graph.steps || []).forEach(function (st) {
      if (st.synthetic || !isWork(st) || st.executor !== "person") return;
      const each = parseDuration(st.duration);
      if (each !== null && st.frequency) minutes += each * st.frequency;
      else unpriced++;
    });
    return { minutes: minutes, unpriced: unpriced };
  }

  // ------------------------------------------------------------------ checks

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  // Findings carry a code so the Python validator and this can be compared
  // pair for pair, and a message so a panel can just print them.
  function checks(graph) {
    const steps = graph.steps || [], links = graph.links || [];
    const known = {}, out = {};
    steps.forEach(function (st) { known[st.id] = st; out[st.id] = []; });
    links.forEach(function (l) { if (out[l.from]) out[l.from].push(l); });

    const findings = [];
    function add(code, severity, step, message, on) {
      findings.push({
        code: code, severity: severity, step: step, message: message,
        id: on ? on.id : null, title: on ? on.title : "",
      });
    }

    const seenNumbers = {};
    steps.forEach(function (st) {
      if (st.number === null || st.number === undefined) return;
      seenNumbers[st.number] = (seenNumbers[st.number] || 0) + 1;
    });
    Object.keys(seenNumbers).forEach(function (n) {
      if (seenNumbers[n] > 1) {
        add("duplicate_step_number", "error", parseInt(n, 10),
            "has more than one step numbered " + n);
      }
    });

    steps.forEach(function (st) {
      if (st.synthetic) return;
      if (STEP_KINDS.indexOf(st.kind) === -1) {
        add("unknown_kind", "error", st.number, "has unknown kind '" + st.kind + "'", st);
      }
      if (EXECUTORS.indexOf(st.executor) === -1) {
        add("unknown_executor", "error", st.number, "has unknown executor '" + st.executor + "'", st);
      }

      const explicit = out[st.id].filter(function (l) { return l.explicit; });
      explicit.forEach(function (l) {
        if (!String(l.label || "").trim()) {
          add("unlabelled_branch", "error", st.number, "has a branch with no label", st);
        }
        const target = known[l.to];
        if (!target) {
          const named = l.to.indexOf("missing:") === 0 ? l.to.slice(8) : l.to;
          add("branch_target_missing", "error", st.number,
              "branches to step " + (named === "none" ? "None" : named) + ", which does not exist", st);
        } else if (st.depth - target.depth > LOOP_BACK_LIMIT) {
          add("long_loop_back", "warning", st.number,
              "loops back " + (st.depth - target.depth) + " steps to " +
              (target.number === null ? target.title : target.number) + " — make that a go-to step", st);
        }
      });

      if (st.kind === "decision" && explicit.length < 2) {
        add("thin_fork", "warning", st.number, "is a decision with fewer than two ways out", st);
      }
      if (st.kind !== "decision" && explicit.length) {
        add("branches_on_non_decision", "warning", st.number, "carries branches but is not a decision", st);
      }

      if (st.kind === "goto") {
        if (st.gotoTarget === null || st.gotoTarget === undefined) {
          add("goto_no_destination", "error", st.number, "is a go-to step with no destination", st);
        } else if (!known[st.gotoTarget]) {
          const named = st.gotoTarget.indexOf("missing:") === 0 ? st.gotoTarget.slice(8) : st.gotoTarget;
          add("goto_target_missing", "error", st.number,
              "jumps to step " + named + ", which does not exist", st);
        }
      }
    });

    const unpriced = monthlyLoad(graph).unpriced;
    if (unpriced) {
      add("unpriced_person_steps", "warning", null,
          plural(unpriced, "person step") + " without both a duration and a monthly frequency");
    }
    return findings;
  }

  // -------------------------------------------------------------- renumbering

  // Steps are numbered by position, so a delete or a move renumbers them and
  // rewrites every branch and go-to target to follow the step it pointed at.
  // A target whose step is gone is unset — never left pointing at whatever
  // moved up into that number.
  function renumber(steps) {
    const map = {};
    steps.forEach(function (st, i) { map[st.number] = i + 1; });
    steps.forEach(function (st, i) { st.number = i + 1; });
    steps.forEach(function (st) {
      (st.branches || []).forEach(function (branch) {
        if (branch.to === "end") return;
        branch.to = Object.prototype.hasOwnProperty.call(map, branch.to) ? map[branch.to] : null;
      });
      if (st.goto !== undefined) {
        st.goto = Object.prototype.hasOwnProperty.call(map, st.goto) ? map[st.goto] : null;
      }
    });
    return steps;
  }

  return {
    STEP_KINDS: STEP_KINDS,
    EXECUTORS: EXECUTORS,
    KIND_LABEL: KIND_LABEL,
    EXECUTOR_LABEL: EXECUTOR_LABEL,
    LOOP_BACK_LIMIT: LOOP_BACK_LIMIT,
    isWork: isWork,
    parseDuration: parseDuration,
    fromProcess: fromProcess,
    fromCanvas: fromCanvas,
    ranks: ranks,
    monthlyLoad: monthlyLoad,
    checks: checks,
    renumber: renumber,
  };
});
