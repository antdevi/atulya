import { promises as fs } from "fs";
import path from "path";
import { execFile } from "child_process";
import { promisify } from "util";
import { NextRequest, NextResponse } from "next/server";

const execFileAsync = promisify(execFile);

function resolveRepoRoot(): string {
  const cwd = process.cwd();
  if (cwd.endsWith(`${path.sep}atulya-control-plane`)) {
    return path.resolve(cwd, "..");
  }
  if (cwd.endsWith(`${path.sep}atulya`)) {
    return cwd;
  }
  return path.resolve(cwd, "..");
}

function artifactPaths(mode: string) {
  const repoRoot = resolveRepoRoot();
  const outputDir = path.join(repoRoot, "atulya-integration-tests", "benchmark-results");
  const jsonName = mode === "live-api" ? "leaderboard.live.json" : "leaderboard.json";
  const markdownName = mode === "live-api" ? "leaderboard.live.md" : "leaderboard.md";
  return {
    repoRoot,
    outputDir,
    jsonPath: path.join(outputDir, jsonName),
    markdownPath: path.join(outputDir, markdownName),
  };
}

async function readArtifacts(mode: string) {
  const { jsonPath, markdownPath } = artifactPaths(mode);
  try {
    const [jsonRaw, markdownRaw, jsonStats] = await Promise.all([
      fs.readFile(jsonPath, "utf-8"),
      fs.readFile(markdownPath, "utf-8"),
      fs.stat(jsonPath),
    ]);
    return {
      available: true,
      leaderboard: JSON.parse(jsonRaw),
      markdown: markdownRaw,
      generated_at: jsonStats.mtime.toISOString(),
    };
  } catch {
    return {
      available: false,
      leaderboard: null,
      markdown: "",
      generated_at: null,
    };
  }
}

export async function GET(request: NextRequest) {
  const mode = request.nextUrl.searchParams.get("mode") || "live-api";
  const artifacts = await readArtifacts(mode);
  return NextResponse.json({ mode, ...artifacts });
}

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({}));
  const mode = body.mode === "deterministic" ? "deterministic" : "live-api";
  const { repoRoot } = artifactPaths(mode);

  try {
    const args = [
      "run",
      "--directory",
      "atulya-integration-tests",
      "atulya-benchmark",
      "--mode",
      mode,
    ];
    const { stdout, stderr } = await execFileAsync("uv", args, {
      cwd: repoRoot,
      maxBuffer: 20 * 1024 * 1024,
    });
    const artifacts = await readArtifacts(mode);
    return NextResponse.json(
      {
        mode,
        stdout,
        stderr,
        ...artifacts,
      },
      { status: 200 }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to run benchmark";
    return NextResponse.json({ error: message, mode }, { status: 500 });
  }
}
