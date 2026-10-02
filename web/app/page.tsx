import { AuthGate } from "@/components/AuthGate";
import { Composer } from "@/components/Composer";
import { FilmGrid } from "@/components/FilmGrid";
import { TopBar } from "@/components/TopBar";

export default function Home() {
  return (
    <AuthGate>
      <main className="page">
        <TopBar />
        <Composer />
        <FilmGrid />
      </main>
    </AuthGate>
  );
}
