import { lazy, Suspense, useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Footer } from "./components/site/Footer";
import { Nav } from "./components/site/Nav";

const Home = lazy(() => import("./pages/Home"));
const Product = lazy(() => import("./pages/Product"));
const Solutions = lazy(() => import("./pages/Solutions"));
const Security = lazy(() => import("./pages/Security"));
const About = lazy(() => import("./pages/About"));

function PageFallback() {
  return (
    <div className="flex min-h-[55vh] items-center justify-center" role="status">
      <span className="h-8 w-8 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
      <span className="sr-only">页面加载中</span>
    </div>
  );
}

/** 路由切换回顶；带 hash（如 /#cta）时平滑滚到锚点。 */
function ScrollManager() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (hash) {
      const el = document.querySelector(hash);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
    }
    window.scrollTo({ top: 0 });
  }, [pathname, hash]);
  return null;
}

export default function App() {
  return (
    <div className="flex min-h-[100dvh] flex-col">
      <ScrollManager />
      <Nav />
      <main className="flex-1">
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/" element={<Home />} />
            {/* 首页保持精简；详细内容保留在独立路径，便于按需深入了解。 */}
            <Route path="/product" element={<Product />} />
            <Route path="/solutions" element={<Solutions />} />
            <Route path="/security" element={<Security />} />
            <Route path="/about" element={<About />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </main>
      <Footer />
    </div>
  );
}
