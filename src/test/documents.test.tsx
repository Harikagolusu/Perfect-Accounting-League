import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  documentTypeToUi,
  fileTypeLabel,
  statusToApi,
  statusToUi,
  toInboxDocument,
  type DocumentDto,
} from "@/lib/api/documents";
import { useDeleteDocument, useDocuments, useUploadDocuments } from "@/lib/queries/documents";

const COMPANY_A = "11111111-1111-1111-1111-111111111111";
const COMPANY_B = "22222222-2222-2222-2222-222222222222";

const PDF: DocumentDto = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  company_id: COMPANY_A,
  original_filename: "invoice_march.pdf",
  file_type: "application/pdf",
  file_size: 2048,
  document_type: "UNKNOWN",
  status: "UPLOADED",
  created_at: "2026-10-19T06:00:00Z",
  updated_at: "2026-10-19T06:00:00Z",
};

const XLSX: DocumentDto = {
  ...PDF,
  id: "aaaaaaaa-0000-0000-0000-000000000002",
  original_filename: "statement.xlsx",
  file_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

const B_DOC: DocumentDto = {
  ...PDF,
  id: "bbbbbbbb-0000-0000-0000-000000000001",
  company_id: COMPANY_B,
  original_filename: "invoice-b.pdf",
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const calls = () => vi.mocked(fetch).mock.calls as unknown as [string, RequestInit][];

/** Only the read requests to the inbox list. */
const listFetches = () =>
  calls().filter(([url, init]) => !init?.method && url.includes("/documents"));

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function ListHarness({ companyId }: { companyId: string | undefined }) {
  const { data, isPending, isError } = useDocuments(companyId);
  if (isPending) return <p>Loading documents</p>;
  if (isError) return <p>Inbox load failed</p>;
  return (
    <ul>
      {data?.map((d) => (
        <li key={d.id}>
          {d.name} — {d.status} — {d.documentType} — {d.fileType} — {d.companyId}
        </li>
      ))}
      {data?.length === 0 && <li>empty inbox</li>}
    </ul>
  );
}

function UploadHarness({ companyId }: { companyId: string }) {
  const upload = useUploadDocuments(companyId);
  return (
    <div>
      <button onClick={() => upload.mutate([new File(["x"], "a.pdf")])}>upload</button>
      {upload.isError ? <p>upload failed</p> : null}
    </div>
  );
}

function DeleteHarness({ companyId }: { companyId: string }) {
  const remove = useDeleteDocument(companyId);
  return <button onClick={() => remove.mutate(PDF.id)}>delete</button>;
}

function renderWith(queryClient: QueryClient, node: ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse([PDF])),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("Document mapper", () => {
  it("maps snake_case onto the UI model", () => {
    const doc = toInboxDocument(PDF);

    expect(doc.id).toBe(PDF.id);
    expect(doc.companyId).toBe(COMPANY_A);
    expect(doc.name).toBe("invoice_march.pdf");
    expect(doc.receivedAt).toBe(PDF.created_at);
    expect(doc.size).toBe(2048);
  });

  it("shows a freshly uploaded document as Unknown", () => {
    expect(toInboxDocument(PDF).documentType).toBe("Unknown");
    expect(toInboxDocument(PDF).status).toBe("Uploaded");
  });

  it("maps every backend status onto a UI status", () => {
    expect(statusToUi("UPLOADED")).toBe("Uploaded");
    expect(statusToUi("PROCESSING")).toBe("Processing");
    expect(statusToUi("PROCESSED")).toBe("Extracted");
    expect(statusToUi("FAILED")).toBe("Failed");
  });

  it("maps UI statuses back onto the API enum", () => {
    expect(statusToApi("Uploaded")).toBe("UPLOADED");
    expect(statusToApi("Processing")).toBe("PROCESSING");
    expect(statusToApi("Extracted")).toBe("PROCESSED");
    expect(statusToApi("Failed")).toBe("FAILED");
  });

  it("maps every backend document type onto a UI label", () => {
    expect(documentTypeToUi("UNKNOWN")).toBe("Unknown");
    expect(documentTypeToUi("INVOICE")).toBe("Invoice");
    expect(documentTypeToUi("BANK_STATEMENT")).toBe("Bank Statement");
    expect(documentTypeToUi("OTHER")).toBe("Other");
  });

  it("labels each supported MIME type for the table", () => {
    expect(fileTypeLabel("application/pdf")).toBe("PDF");
    expect(fileTypeLabel("image/jpeg")).toBe("JPEG");
    expect(fileTypeLabel("image/png")).toBe("PNG");
    expect(fileTypeLabel("application/vnd.ms-excel")).toBe("XLS");
    expect(toInboxDocument(XLSX).fileType).toBe("XLSX");
  });

  it("marks inbox documents as manual uploads awaiting a reviewer", () => {
    expect(toInboxDocument(PDF).source).toBe("Manual Upload");
    expect(toInboxDocument(PDF).reviewer).toBe("Unassigned");
  });
});

describe("Document queries are company-scoped", () => {
  it("requests the company's own inbox path", async () => {
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(/invoice_march/)).toBeTruthy());

    expect(calls()[0][0]).toContain(`/api/v1/companies/${COMPANY_A}/documents`);
  });

  it("renders only the documents returned for that company", async () => {
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(/invoice_march/)).toBeTruthy());

    expect(screen.queryByText(/invoice-b/)).toBeNull();
  });

  it("shows a loading state before the documents arrive", () => {
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);

    expect(screen.getByText("Loading documents")).toBeTruthy();
  });

  it("shows an empty state for a company with no documents", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);

    await waitFor(() => expect(screen.getByText("empty inbox")).toBeTruthy());
  });

  it("shows an error rather than falling back to mock documents", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Boom" }, 500)),
    );
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);

    await waitFor(() => expect(screen.getByText("Inbox load failed")).toBeTruthy());
    // Crucially: no sample document is substituted for the failure.
    expect(screen.queryByText(/invoice_march/)).toBeNull();
  });

  it("does not call the API without a company id", async () => {
    renderWith(makeClient(), <ListHarness companyId={undefined} />);

    await waitFor(() => expect(screen.getByText("Loading documents")).toBeTruthy());
    expect(calls()).toHaveLength(0);
  });

  it("keeps each company's inbox cache separate", async () => {
    const shared = makeClient();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => jsonResponse(String(url).includes(COMPANY_A) ? [PDF] : [B_DOC])),
    );

    const first = renderWith(shared, <ListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(/invoice_march/)).toBeTruthy());
    first.unmount();

    renderWith(shared, <ListHarness companyId={COMPANY_B} />);
    await waitFor(() => expect(screen.getByText(/invoice-b/)).toBeTruthy());
    expect(screen.queryByText(/invoice_march/)).toBeNull();
  });
});

describe("Document mutations", () => {
  it("uploads as multipart form data", async () => {
    renderWith(makeClient(), <UploadHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("upload"));

    await waitFor(() => {
      const post = calls().find(([, init]) => init?.method === "POST");
      expect(post).toBeTruthy();
      expect(post![1].body).toBeInstanceOf(FormData);
      expect((post![1].body as FormData).get("file")).toBeInstanceOf(File);
    });
  });

  it("does not force a Content-Type on the upload", async () => {
    // The browser must set the multipart boundary itself.
    renderWith(makeClient(), <UploadHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("upload"));

    await waitFor(() => {
      const post = calls().find(([, init]) => init?.method === "POST");
      expect((post![1].headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    });
  });

  it("surfaces an upload failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "POST"
          ? jsonResponse({ detail: "Unsupported file type '.txt'" }, 415)
          : jsonResponse([PDF]),
      ),
    );
    renderWith(makeClient(), <UploadHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("upload"));

    await waitFor(() => expect(screen.getByText("upload failed")).toBeTruthy());
  });

  it("deletes with DELETE against the company path", async () => {
    renderWith(makeClient(), <DeleteHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("delete"));

    await waitFor(() => {
      const del = calls().find(([url, init]) => init?.method === "DELETE");
      expect(del).toBeTruthy();
      expect(del![0]).toContain(`/api/v1/companies/${COMPANY_A}/documents/${PDF.id}`);
    });
  });

  it("refetches the inbox after a delete", async () => {
    // The list query must be mounted for invalidation to have something to
    // refetch, so this case mounts both halves.
    const shared = makeClient();
    renderWith(
      shared,
      <>
        <ListHarness companyId={COMPANY_A} />
        <DeleteHarness companyId={COMPANY_A} />
      </>,
    );
    await waitFor(() => expect(screen.getByText(/invoice_march/)).toBeTruthy());
    expect(listFetches()).toHaveLength(1);

    fireEvent.click(screen.getByText("delete"));

    // The delete invalidates the company's list, which is then fetched again.
    await waitFor(() => expect(listFetches()).toHaveLength(2));
  });
});
