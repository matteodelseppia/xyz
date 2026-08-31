import { S3Client } from "bun";
import { resolve, sep } from "node:path";

export interface StoredObject { data: Uint8Array; contentType: string }
export interface Storage { get(key: string): Promise<StoredObject | null> }

export function safeKey(key: string): string {
  if (!key || key.startsWith("/") || key.includes("\\") || key.split("/").includes("..")) throw new Error("unsafe storage key");
  return key;
}

export class LocalStorage implements Storage {
  private readonly root: string;
  constructor(root: string) { this.root = resolve(root); }
  async get(key: string): Promise<StoredObject | null> {
    const path = resolve(this.root, safeKey(key));
    if (path !== this.root && !path.startsWith(this.root + sep)) throw new Error("storage path escaped root");
    const file = Bun.file(path);
    if (!(await file.exists())) return null;
    let contentType = "application/octet-stream";
    const metadata = Bun.file(`${path}.metadata`);
    if (await metadata.exists()) contentType = (await metadata.text()).trim();
    return { data: new Uint8Array(await file.arrayBuffer()), contentType };
  }
}

export class ObjectStorage implements Storage {
  private readonly client: S3Client;
  constructor() {
    const bucket = process.env.XYZ_S3_BUCKET;
    const accessKeyId = process.env.XYZ_S3_ACCESS_KEY_ID;
    const secretAccessKey = process.env.XYZ_S3_SECRET_ACCESS_KEY;
    if (!bucket || !accessKeyId || !secretAccessKey) throw new Error("object storage configuration is incomplete");
    this.client = new S3Client({
      bucket, accessKeyId, secretAccessKey,
      endpoint: process.env.XYZ_S3_ENDPOINT_URL,
      region: process.env.XYZ_S3_REGION ?? "auto",
    });
  }
  async get(key: string): Promise<StoredObject | null> {
    const file = this.client.file(safeKey(key));
    if (!(await file.exists())) return null;
    return { data: new Uint8Array(await file.arrayBuffer()), contentType: file.type || "application/octet-stream" };
  }
}

export function storageFromEnvironment(): Storage {
  return process.env.XYZ_STORAGE === "s3"
    ? new ObjectStorage()
    : new LocalStorage(process.env.XYZ_STORAGE_ROOT ?? "storage");
}
