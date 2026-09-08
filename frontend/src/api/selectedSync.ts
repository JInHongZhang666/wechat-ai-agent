/** AIHOT selected-set mirror client with one bounded snapshot recovery. */

import type {
  PublicItem,
  PublicItemMinimal,
} from '../types/api-v1.generated';
import { api, ApiClientError } from './client';

export type SelectedMirrorItem = PublicItem | PublicItemMinimal;
export type SelectedFieldMode = 'default' | 'minimal';

export interface SelectedMirror {
  fields: SelectedFieldMode;
  cursor: string;
  items: Record<string, SelectedMirrorItem>;
}

export interface SelectedMirrorStore {
  load(): Promise<SelectedMirror | null>;
  /** Implementations must replace items and cursor in one atomic write. */
  save(mirror: SelectedMirror): Promise<void>;
}

export async function bootstrapSelectedMirror(
  store: SelectedMirrorStore,
  fields: SelectedFieldMode = 'minimal',
  limit = 500,
): Promise<SelectedMirror> {
  const items: Record<string, SelectedMirrorItem> = {};
  let page: string | undefined;
  let snapshotCursor: string | undefined;

  for (let pageCount = 0; pageCount < 10000; pageCount += 1) {
    const response = await api.getSelectedSnapshot({ fields, limit, page });
    if (snapshotCursor && response.cursor !== snapshotCursor) {
      throw new Error('Snapshot cursor changed during one frozen snapshot');
    }
    snapshotCursor = response.cursor;
    for (const item of response.items) items[item.id] = item;
    if (!response.hasMore) {
      const mirror = { fields, cursor: response.cursor, items };
      await store.save(mirror);
      return mirror;
    }
    if (!response.nextPage) {
      throw new Error('Snapshot declared more pages without nextPage');
    }
    page = response.nextPage;
  }
  throw new Error('Snapshot exceeded the bounded page limit');
}

export async function drainSelectedChanges(
  store: SelectedMirrorStore,
  mirror: SelectedMirror,
  limit = 100,
): Promise<SelectedMirror> {
  let current = mirror;
  for (let pageCount = 0; pageCount < 10000; pageCount += 1) {
    const response = await api.getSelectedChanges(current.cursor, limit);
    const nextItems = { ...current.items };
    for (const change of response.changes) {
      if (change.op === 'upsert') nextItems[change.id] = change.item;
      else delete nextItems[change.id];
    }
    current = {
      fields: response.fields,
      cursor: response.cursor,
      items: nextItems,
    };
    // Save only after every mutation in this page has been applied.
    await store.save(current);
    if (!response.hasMore) return current;
  }
  throw new Error('Changes backlog exceeded the bounded page limit');
}

export async function syncSelectedMirror(
  store: SelectedMirrorStore,
  fields: SelectedFieldMode = 'minimal',
): Promise<SelectedMirror> {
  const existing = await store.load();
  if (!existing || existing.fields !== fields) {
    return bootstrapSelectedMirror(store, fields);
  }
  try {
    return await drainSelectedChanges(store, existing);
  } catch (error) {
    if (error instanceof ApiClientError && error.code === 'SNAPSHOT_REQUIRED') {
      // Exactly one bounded rebuild; failures propagate to the caller.
      return bootstrapSelectedMirror(store, fields);
    }
    throw error;
  }
}
