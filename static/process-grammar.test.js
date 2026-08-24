/*
 * Tests for process-grammar.js.
 *
 *   node static/process-grammar.test.js                 the suite
 *   node static/process-grammar.test.js --cross f.json  reconcile against Python
 *
 * The second mode is what tests/test_process_grammar.py drives: it hands over
 * the same processes it ran through check_process() in
 * tools/blueprint_converter.py and compares the findings code for code, so the
 * browser tools and the validator cannot drift into disagreeing about whether
 * a map is finished. The suite below covers what Python has no counterpart for
 * — the canvas adapter, the layering, the duration parser and the renumbering.
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const G = require("./process-grammar.js");

/* ------------------------------------------------ cross-check mode */

const crossAt = process.argv.indexOf("--cross");
if (crossAt !== -1) {
  const spec = JSON.parse(fs.readFileSync(process.argv[crossAt + 1], "utf8"));
  const results = spec.cases.map(c => ({
    name: c.name,
    findings: G.checks(G.fromProcess(c.process)).map(f => ({
      code: f.code,
      step: f.step === undefined ? null : f.step,
      severity: f.severity,
    })),
  }));
  process.stdout.write(JSON.stringify({ results: results }));
  process.exit(0);
}

/* ------------------------------------------------ suite */

let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed++; }
  catch (e) { failures.push(name + ": " + e.message); }
}
function codes(graph) {
  return G.checks(graph).map(f => f.code).sort();
}

/* --- duration --- */

test("compact durations", () => {
  assert.strictEqual(G.parseDuration("45m"), 45);
  assert.strictEqual(G.parseDuration("90"), 90);
  assert.strictEqual(G.parseDuration("1h30m"), 90);
  assert.strictEqual(G.parseDuration("1h 30m"), 90);
  assert.strictEqual(G.parseDuration("1.5h"), 90);
});

test("a day of work is eight hours", () => {
  assert.strictEqual(G.parseDuration("2d"), 960);
  assert.strictEqual(G.parseDuration("1 day"), 480);
});

test("blueprint prose", () => {
  assert.strictEqual(G.parseDuration("30 minutes"), 30);
  assert.strictEqual(G.parseDuration("1 hour"), 60);
  assert.strictEqual(G.parseDuration("4 hours"), 240);
  assert.strictEqual(G.parseDuration("instant"), 0);
});

test("a range reads as its midpoint", () => {
  assert.strictEqual(G.parseDuration("2-8 hours"), 300);
  assert.strictEqual(G.parseDuration("2 to 8 hours"), 300);
});

test("prose that is not a duration stays unread", () => {
  ["ongoing", "continuous", "varies", "about an hour", "45m ish",
   "3 fortnights", "up to 1 trading day", "", null, undefined
  ].forEach(v => assert.strictEqual(G.parseDuration(v), null, JSON.stringify(v)));
});

/* --- the blueprint adapter --- */

const LINEAR = { steps: [
  { number: 1, title: "One", duration: "10 minutes", frequency: 4 },
  { number: 2, title: "Two", duration: "10 minutes", frequency: 4 },
  { number: 3, title: "Three", duration: "10 minutes", frequency: 4 },
]};

test("a linear process falls through", () => {
  const g = G.fromProcess(LINEAR);
  assert.strictEqual(g.steps.length, 3);
  assert.strictEqual(g.links.length, 2);
  assert.ok(g.links.every(l => !l.explicit));
  assert.deepStrictEqual(codes(g), []);
});

test("a terminal branch becomes a real terminator", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "Fork", kind: "decision", duration: "1 minute", frequency: 4,
      branches: [{ label: "Yes", to: 2 }, { label: "No", to: "end" }] },
    { number: 2, title: "Carry on", duration: "5 minutes", frequency: 4 },
  ]});
  const ends = g.steps.filter(s => s.kind === "end");
  assert.strictEqual(ends.length, 1);
  assert.strictEqual(ends[0].title, "End — No");
  assert.ok(ends[0].synthetic);
  assert.deepStrictEqual(codes(g), []);   // two ways out, both labelled
});

test("a go-to resolves to its destination", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "One", duration: "5 minutes", frequency: 4 },
    { number: 2, title: "Go back", kind: "goto", goto: 1 },
  ]});
  const jump = g.steps.filter(s => s.kind === "goto")[0];
  assert.strictEqual(jump.gotoTarget, "s1");
  assert.deepStrictEqual(codes(g), []);
  // and it ends its branch: nothing flows out of it
  assert.strictEqual(g.links.filter(l => l.from === jump.id).length, 0);
});

test("steps out of order are sorted by number", () => {
  const g = G.fromProcess({ steps: [
    { number: 3, title: "Third" }, { number: 1, title: "First" }, { number: 2, title: "Second" },
  ]});
  assert.deepStrictEqual(g.steps.map(s => s.title), ["First", "Second", "Third"]);
});

/* --- every finding --- */

test("a branch pointing nowhere", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "Fork", kind: "decision", duration: "1 minute", frequency: 4,
      branches: [{ label: "Yes", to: 2 }, { label: "No", to: 99 }] },
    { number: 2, title: "Two", duration: "5 minutes", frequency: 4 },
  ]});
  assert.deepStrictEqual(codes(g), ["branch_target_missing"]);
});

test("an unlabelled branch", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "Fork", kind: "decision", duration: "1 minute", frequency: 4,
      branches: [{ label: "", to: 2 }, { label: "No", to: "end" }] },
    { number: 2, title: "Two", duration: "5 minutes", frequency: 4 },
  ]});
  assert.deepStrictEqual(codes(g), ["unlabelled_branch"]);
});

test("a fork with one way out", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "Fork", kind: "decision", duration: "1 minute", frequency: 4,
      branches: [{ label: "Yes", to: 2 }] },
    { number: 2, title: "Two", duration: "5 minutes", frequency: 4 },
  ]});
  assert.deepStrictEqual(codes(g), ["thin_fork"]);
});

test("branches on something that is not a fork", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "Task", duration: "1 minute", frequency: 4,
      branches: [{ label: "Yes", to: 2 }] },
    { number: 2, title: "Two", duration: "5 minutes", frequency: 4 },
  ]});
  assert.deepStrictEqual(codes(g), ["branches_on_non_decision"]);
});

test("a go-to with no destination", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "One", duration: "5 minutes", frequency: 4 },
    { number: 2, title: "Jump", kind: "goto" },
  ]});
  assert.deepStrictEqual(codes(g), ["goto_no_destination"]);
});

test("a go-to pointing at a step that is not there", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "One", duration: "5 minutes", frequency: 4 },
    { number: 2, title: "Jump", kind: "goto", goto: 42 },
  ]});
  assert.deepStrictEqual(codes(g), ["goto_target_missing"]);
});

test("a long loop-back wants a go-to step", () => {
  const steps = [1, 2, 3, 4, 5].map(n => ({
    number: n, title: "Step " + n, duration: "5 minutes", frequency: 4 }));
  steps[4] = { number: 5, title: "Fork", kind: "decision", duration: "1 minute", frequency: 4,
               branches: [{ label: "Again", to: 1 }, { label: "On", to: "end" }] };
  assert.deepStrictEqual(codes(G.fromProcess({ steps: steps })), ["long_loop_back"]);
});

test("a loop-back within three steps is fine", () => {
  const steps = [1, 2, 3, 4, 5].map(n => ({
    number: n, title: "Step " + n, duration: "5 minutes", frequency: 4 }));
  steps[4] = { number: 5, title: "Fork", kind: "decision", duration: "1 minute", frequency: 4,
               branches: [{ label: "Again", to: 5 - G.LOOP_BACK_LIMIT }, { label: "On", to: "end" }] };
  assert.deepStrictEqual(codes(G.fromProcess({ steps: steps })), []);
});

test("a long go-to does not warn — reaching far is the point of one", () => {
  const steps = [1, 2, 3, 4, 5, 6].map(n => ({
    number: n, title: "Step " + n, duration: "5 minutes", frequency: 4 }));
  steps[5] = { number: 6, title: "Go to: Step 1", kind: "goto", goto: 1 };
  assert.deepStrictEqual(codes(G.fromProcess({ steps: steps })), []);
});

test("duplicate step numbers", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "One", duration: "5 minutes", frequency: 4 },
    { number: 1, title: "Also one", duration: "5 minutes", frequency: 4 },
  ]});
  assert.ok(codes(g).indexOf("duplicate_step_number") > -1);
});

test("unknown kind and executor", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "One", kind: "wibble", executor: "robot" },
  ]});
  const c = codes(g);
  assert.ok(c.indexOf("unknown_kind") > -1);
  assert.ok(c.indexOf("unknown_executor") > -1);
});

/* --- costing --- */

test("duration times frequency, person steps only", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "A", executor: "person", duration: "10 minutes", frequency: 40 },
    { number: 2, title: "B", executor: "automation", duration: "2 hours", frequency: 40 },
    { number: 3, title: "C", executor: "person", duration: "1 hour", frequency: 2 },
  ]});
  assert.strictEqual(G.monthlyLoad(g).minutes, 10 * 40 + 60 * 2);
  assert.strictEqual(G.monthlyLoad(g).unpriced, 0);
});

test("either number missing leaves the step unpriced", () => {
  const half = G.fromProcess({ steps: [
    { number: 1, title: "A", executor: "person", duration: "10 minutes" },
    { number: 2, title: "B", executor: "person", frequency: 4 },
    { number: 3, title: "C", executor: "person" },
  ]});
  assert.strictEqual(G.monthlyLoad(half).minutes, 0);
  assert.strictEqual(G.monthlyLoad(half).unpriced, 3);
  assert.deepStrictEqual(codes(half), ["unpriced_person_steps"]);
});

test("waits, terminators and jumps are not labour", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "Hold", kind: "delay", executor: "person", duration: "2 days" },
    { number: 2, title: "Jump", kind: "goto", executor: "person", goto: 1 },
  ]});
  assert.strictEqual(G.monthlyLoad(g).minutes, 0);
  assert.strictEqual(G.monthlyLoad(g).unpriced, 0);
});

test("a synthetic terminator is not a step somebody has to price", () => {
  const g = G.fromProcess({ steps: [
    { number: 1, title: "Fork", kind: "decision", executor: "person",
      duration: "1 minute", frequency: 4,
      branches: [{ label: "Yes", to: 2 }, { label: "No", to: "end" }] },
    { number: 2, title: "Two", executor: "person", duration: "5 minutes", frequency: 4 },
  ]});
  assert.strictEqual(G.monthlyLoad(g).unpriced, 0);
});

/* --- the canvas adapter --- */

const CANVAS = {
  nodes: [
    { id: "a", title: "Start", kind: "step", owner: "person", dur: "60m", freq: 2 },
    { id: "b", title: "Fork?", kind: "step", owner: "person", dur: "45m", freq: 2, decision: true },
    { id: "c", title: "Left", kind: "step", owner: "auto", dur: "120m" },
    { id: "d", title: "Right", kind: "step", owner: "ai", dur: "180m" },
    { id: "e", title: "Finish", kind: "step", owner: "person", dur: "60m", freq: 1 },
    { id: "f", title: "Go to: Start", kind: "goto", target: "a" },
  ],
  edges: [
    { from: "a", to: "b", label: "" },
    { from: "b", to: "c", label: "left" },
    { from: "b", to: "d", label: "right" },
    { from: "c", to: "e", label: "" },
    { from: "d", to: "e", label: "" },
    { from: "e", to: "f", label: "next" },
  ],
};

test("a canvas map converts and passes", () => {
  const g = G.fromCanvas(CANVAS);
  assert.strictEqual(g.steps.length, 6);
  assert.deepStrictEqual(codes(g), []);
  assert.strictEqual(G.monthlyLoad(g).minutes, 60 * 2 + 45 * 2 + 60 * 1);
});

test("canvas owners map onto executors", () => {
  const g = G.fromCanvas(CANVAS);
  const by = {};
  g.steps.forEach(s => { by[s.title] = s.executor; });
  assert.strictEqual(by["Start"], "person");
  assert.strictEqual(by["Left"], "automation");
  assert.strictEqual(by["Right"], "ai");
});

test("only wires out of a fork count as branches", () => {
  const g = G.fromCanvas(CANVAS);
  const explicit = g.links.filter(l => l.explicit).map(l => l.label).sort();
  assert.deepStrictEqual(explicit, ["left", "right"]);
});

test("an unlabelled wire out of a canvas fork is caught", () => {
  const broken = JSON.parse(JSON.stringify(CANVAS));
  broken.edges[1].label = "";
  assert.deepStrictEqual(codes(G.fromCanvas(broken)), ["unlabelled_branch"]);
});

test("a canvas loop-back over three layers wants a go-to step", () => {
  const broken = JSON.parse(JSON.stringify(CANVAS));
  broken.nodes[5].kind = "step";            // an ordinary step again...
  broken.nodes[5].owner = "auto";
  broken.edges.push({ from: "f", to: "a", label: "again" });
  broken.nodes[4].decision = true;          // ...so the wire out of it is a branch
  broken.edges[5].label = "next";
  const c = codes(G.fromCanvas(broken));
  assert.ok(c.indexOf("long_loop_back") === -1, "an ordinary wire is not a branch");
  broken.nodes[5].decision = true;
  broken.edges.push({ from: "f", to: "e", label: "on" });
  assert.ok(codes(G.fromCanvas(broken)).indexOf("long_loop_back") > -1);
});

test("layers follow the longest path", () => {
  const rank = G.ranks(G.fromCanvas(CANVAS));
  assert.strictEqual(rank.a, 0);
  assert.strictEqual(rank.b, 1);
  assert.strictEqual(rank.e, 3);
  assert.strictEqual(rank.f, 4);
});

/* --- renumbering --- */

function sample() {
  return [
    { number: 1, title: "One" },
    { number: 2, title: "Two", kind: "decision",
      branches: [{ label: "a", to: 3 }, { label: "b", to: "end" }] },
    { number: 3, title: "Three" },
    { number: 4, title: "Jump", kind: "goto", goto: 1 },
  ];
}

test("a move repoints everything at the step it followed", () => {
  const steps = sample();
  const held = steps[2]; steps[2] = steps[3]; steps[3] = held;   // swap 3 and 4
  G.renumber(steps);
  assert.deepStrictEqual(steps.map(s => s.number), [1, 2, 3, 4]);
  const fork = steps.filter(s => s.kind === "decision")[0];
  assert.strictEqual(fork.branches[0].to, 4, "the branch followed Three");
  assert.strictEqual(fork.branches[1].to, "end", "a terminal branch is untouched");
  assert.strictEqual(steps.filter(s => s.kind === "goto")[0].goto, 1);
});

test("deleting a target unsets what pointed at it", () => {
  const steps = sample();
  steps.splice(2, 1);                                            // delete Three
  G.renumber(steps);
  assert.deepStrictEqual(steps.map(s => s.number), [1, 2, 3]);
  assert.strictEqual(steps.filter(s => s.kind === "decision")[0].branches[0].to, null);
  assert.strictEqual(steps.filter(s => s.kind === "goto")[0].goto, 1);
});

test("an unset branch is then reported, not hidden", () => {
  const steps = sample();
  steps.splice(2, 1);
  G.renumber(steps);
  assert.ok(codes(G.fromProcess({ steps: steps })).indexOf("branch_target_missing") > -1);
});

failures.forEach(f => console.error("FAIL  " + f));
console.log(passed + " passed, " + failures.length + " failed");
process.exit(failures.length ? 1 : 0);
