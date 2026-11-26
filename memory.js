import fs from 'node:fs/promises';
import path from 'node:path';
import { CONFIG } from './config.js';

const MEMORY_PATH = path.resolve(CONFIG.MEMORY_FILE);

export async function loadMemory() {
  try {
    const raw = await fs.readFile(MEMORY_PATH, 'utf8');
    return JSON.parse(raw);
  } catch {
    return { chats: {} };
  }
}

export async function saveMemory(mem) {
  const tmp = `${MEMORY_PATH}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(mem, null, 2), 'utf8');
  await fs.rename(tmp, MEMORY_PATH);
}

export function getChatState(mem, chatId) {
  const key = String(chatId);
  if (!mem.chats[key]) {
    mem.chats[key] = {
      preferredModel: CONFIG.PRIMARY_MODEL,
      projectName: null,
      lastUrl: null,
      repoUrl: null,
      files: [],
      history: [],
    };
  }
  return mem.chats[key];
}

// Merges updated/new files into existing project files without destroying untouched files
export function mergeFiles(existingFiles = [], incomingFiles = []) {
  const map = new Map();
  for (const f of existingFiles) {
    map.set(f.file, f.data);
  }
  for (const f of incomingFiles) {
    map.set(f.file, f.data);
  }
  return Array.from(map.entries()).map(([file, data]) => ({ file, data }));
}
