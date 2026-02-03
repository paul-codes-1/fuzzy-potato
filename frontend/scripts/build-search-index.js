#!/usr/bin/env node
/**
 * Build FlexSearch index from transcript and minutes files.
 *
 * Reads all transcript_*.txt and minutes_*.txt files from lfucg_output/clips/
 * and creates chunked FlexSearch indexes for full-text search.
 *
 * Usage: node scripts/build-search-index.js
 * Output: public/data/search_index_manifest.json + search_index_chunk_N.json
 */

import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

const CLIPS_DIR = path.resolve(__dirname, '../../lfucg_output/clips')
const OUTPUT_DIR = path.resolve(__dirname, '../public/data')
const INDEX_JSON = path.resolve(__dirname, '../../lfucg_output/index.json')

// Target chunk size in bytes (~1.5MB to stay comfortably under 2MB)
const TARGET_CHUNK_SIZE = 1.5 * 1024 * 1024

function readTextFile(filePath) {
  try {
    return fs.readFileSync(filePath, 'utf-8')
  } catch {
    return null
  }
}

function findTranscriptFile(clipDir) {
  const files = fs.readdirSync(clipDir)
  const transcriptFile = files.find(f => f.startsWith('transcript_') && f.endsWith('.txt'))
  return transcriptFile ? path.join(clipDir, transcriptFile) : null
}

function findMinutesFile(clipDir, clipId) {
  const minutesPath = path.join(clipDir, `minutes_${clipId}.txt`)
  return fs.existsSync(minutesPath) ? minutesPath : null
}

function buildIndex() {
  console.log('Building FlexSearch index...')

  // Read index.json to get metadata for each clip
  let metadata = {}
  try {
    const indexData = JSON.parse(fs.readFileSync(INDEX_JSON, 'utf-8'))
    // Handle both array format and {clips: [...]} format
    const clips = Array.isArray(indexData) ? indexData : indexData.clips || []
    clips.forEach(item => {
      metadata[item.clip_id] = {
        title: item.title,
        date: item.date,
        meeting_body: item.meeting_body,
        topics: item.topics
      }
    })
    console.log(`Loaded metadata for ${Object.keys(metadata).length} clips from index.json`)
  } catch (err) {
    console.warn('Warning: Could not load index.json, using clip directories only')
    console.warn('Error:', err.message)
  }

  // Get all clip directories
  const clipDirs = fs.readdirSync(CLIPS_DIR)
    .filter(name => /^\d+$/.test(name))
    .map(name => ({
      clipId: parseInt(name, 10),
      path: path.join(CLIPS_DIR, name)
    }))
    .filter(({ path: clipPath }) => fs.statSync(clipPath).isDirectory())

  console.log(`Found ${clipDirs.length} clip directories`)

  // Build documents array for the index
  const documents = []
  let transcriptCount = 0
  let minutesCount = 0

  for (const { clipId, path: clipPath } of clipDirs) {
    const transcriptPath = findTranscriptFile(clipPath)
    const minutesPath = findMinutesFile(clipPath, clipId)

    const transcript = transcriptPath ? readTextFile(transcriptPath) : null
    const minutes = minutesPath ? readTextFile(minutesPath) : null

    // Skip clips with no searchable content
    if (!transcript && !minutes) continue

    const meta = metadata[clipId] || {}
    const content = [transcript, minutes].filter(Boolean).join('\n\n---\n\n')

    documents.push({
      id: clipId,
      title: meta.title || `Clip ${clipId}`,
      date: meta.date || null,
      meeting_body: meta.meeting_body || null,
      topics: meta.topics || [],
      content
    })

    if (transcript) transcriptCount++
    if (minutes) minutesCount++
  }

  console.log(`Indexed ${documents.length} clips (${transcriptCount} transcripts, ${minutesCount} minutes)`)

  // Ensure output directory exists
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true })
  }

  // Clean up old chunk files
  const existingFiles = fs.readdirSync(OUTPUT_DIR)
  for (const file of existingFiles) {
    if (file.startsWith('search_index_chunk_') || file === 'search_index_manifest.json' || file === 'search_index.json') {
      fs.unlinkSync(path.join(OUTPUT_DIR, file))
    }
  }

  // Calculate total size and determine chunking
  const totalSize = documents.reduce((sum, doc) => sum + JSON.stringify(doc).length, 0)
  const numChunks = Math.max(1, Math.ceil(totalSize / TARGET_CHUNK_SIZE))
  const docsPerChunk = Math.ceil(documents.length / numChunks)

  console.log(`Total content size: ${(totalSize / 1024 / 1024).toFixed(2)} MB`)
  console.log(`Splitting into ${numChunks} chunk(s) (~${docsPerChunk} clips each)`)

  // Split documents into chunks
  const chunks = []
  for (let i = 0; i < numChunks; i++) {
    const start = i * docsPerChunk
    const end = Math.min(start + docsPerChunk, documents.length)
    chunks.push(documents.slice(start, end))
  }

  // Write chunk files
  const chunkFiles = []
  for (let i = 0; i < chunks.length; i++) {
    const chunkData = {
      version: 1,
      chunkIndex: i,
      totalChunks: chunks.length,
      documents: chunks[i]
    }

    const filename = `search_index_chunk_${i + 1}.json`
    const filepath = path.join(OUTPUT_DIR, filename)
    fs.writeFileSync(filepath, JSON.stringify(chunkData))

    const stats = fs.statSync(filepath)
    const sizeMB = (stats.size / 1024 / 1024).toFixed(2)
    console.log(`  Wrote ${filename} (${sizeMB} MB, ${chunks[i].length} clips)`)

    chunkFiles.push({
      filename,
      size: stats.size,
      clipCount: chunks[i].length,
      clipIds: chunks[i].map(d => d.id)
    })
  }

  // Write manifest file
  const manifest = {
    version: 1,
    created: new Date().toISOString(),
    totalClips: documents.length,
    totalChunks: chunks.length,
    chunks: chunkFiles
  }

  const manifestPath = path.join(OUTPUT_DIR, 'search_index_manifest.json')
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2))
  console.log(`Wrote search_index_manifest.json`)

  const totalSizeMB = chunkFiles.reduce((sum, c) => sum + c.size, 0) / 1024 / 1024
  console.log(`\nTotal index size: ${totalSizeMB.toFixed(2)} MB across ${chunks.length} chunk(s)`)
}

buildIndex()
