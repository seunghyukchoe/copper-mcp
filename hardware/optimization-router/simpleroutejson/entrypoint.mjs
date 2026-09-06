import process from "node:process";
import { AutoroutingPipelineSolver } from "@tscircuit/capacity-autorouter";

const chunks = [];
let inputBytes = 0;
const MAX_INPUT_BYTES = 16 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 16 * 1024 * 1024;
const MAX_STEPS = 1_000_000;
for await (const chunk of process.stdin) {
  inputBytes += chunk.length;
  if (inputBytes > MAX_INPUT_BYTES) process.exit(64);
  chunks.push(chunk);
}
const input = Buffer.concat(chunks);
if (input.length === 0) process.exit(64);

let problem;
try {
  problem = JSON.parse(input.toString("utf8"));
} catch {
  process.exit(64);
}

const solver = new AutoroutingPipelineSolver(problem);
let steps = 0;
while (!solver.solved && !solver.failed && steps++ < MAX_STEPS) solver.step();
if (solver.failed) process.exit(65);
if (!solver.solved) process.exit(65);
const output = Buffer.from(JSON.stringify(solver.getOutputSimpleRouteJson()));
if (output.length === 0 || output.length > MAX_OUTPUT_BYTES) process.exit(65);
process.stdout.write(output);
