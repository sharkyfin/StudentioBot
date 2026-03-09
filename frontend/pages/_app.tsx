import '../styles/globals.css';
import type { AppProps } from 'next/app';
import 'katex/dist/katex.min.css';
import { Sidebar } from '../components/Sidebar';

export default function MyApp({ Component, pageProps }: AppProps) {
    return (
        <div className="min-h-screen bg-bg text-white flex">
            <Sidebar />
            <main className="flex-1 p-4 sm:p-6 lg:p-8">
                <Component {...pageProps} />
            </main>
        </div>
    );
}
