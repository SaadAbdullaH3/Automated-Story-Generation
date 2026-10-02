import { Suspense } from "react";

import { AuthGate } from "@/components/AuthGate";
import { Studio } from "@/components/studio/Studio";
import { TopBar } from "@/components/TopBar";

// One static page for every project: the id is read from ?id= in the browser,
// which is what lets the whole app be a static export served by FastAPI.
export default function StudioPage() {
  return (
    <AuthGate>
      <main className="page">
        <TopBar />
        <Suspense fallback={null}>
          <Studio />
        </Suspense>
      </main>
    </AuthGate>
  );
}
