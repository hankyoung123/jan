#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const baseConfigPath = resolve(repositoryRoot, 'src-tauri/tauri.conf.json')

function fail(message) {
  console.error(`release config error: ${message}`)
  process.exitCode = 1
}

function readOption(args, name) {
  const index = args.indexOf(name)
  if (index === -1) return undefined
  const value = args[index + 1]
  if (!value || value.startsWith('--')) {
    throw new Error(`${name} requires a value`)
  }
  return value
}

function requireEnv(name) {
  const value = process.env[name]?.trim()
  if (!value) throw new Error(`${name} is required`)
  return value
}

function validateVersion(version) {
  if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(version)) {
    throw new Error(
      'STORY_ENGINE_VERSION must use semver without a leading "v" (for example 0.2.0)'
    )
  }
}

function validateEndpoint(endpoint) {
  let parsed
  try {
    parsed = new URL(endpoint)
  } catch {
    throw new Error('STORY_ENGINE_UPDATER_ENDPOINT must be a valid URL')
  }
  if (parsed.protocol !== 'https:') {
    throw new Error('STORY_ENGINE_UPDATER_ENDPOINT must use HTTPS')
  }
}

async function main() {
  const args = process.argv.slice(2)
  const output = resolve(
    repositoryRoot,
    readOption(args, '--output') ?? '.build/tauri.release.conf.json'
  )
  const version = requireEnv('STORY_ENGINE_VERSION')
  const endpoint = requireEnv('STORY_ENGINE_UPDATER_ENDPOINT')
  const publicKey = requireEnv('TAURI_UPDATER_PUBLIC_KEY')

  validateVersion(version)
  validateEndpoint(endpoint)

  const config = JSON.parse(await readFile(baseConfigPath, 'utf8'))
  config.version = version
  config.plugins ??= {}
  config.plugins.updater ??= {}
  config.plugins.updater.endpoints = [endpoint]
  config.plugins.updater.pubkey = publicKey
  config.bundle ??= {}
  config.bundle.createUpdaterArtifacts = true

  await mkdir(dirname(output), { recursive: true })
  await writeFile(output, `${JSON.stringify(config, null, 2)}\n`, 'utf8')
  console.log(`wrote ${output}`)
}

try {
  await main()
} catch (error) {
  fail(error instanceof Error ? error.message : String(error))
}
