import { Outlet } from 'react-router-dom'
import { useAuth } from '@clerk/clerk-react'
import Sidebar from './Sidebar'
import SampleBanner from '../SampleBanner'

export default function Layout() {
  // TW-295: unauthenticated visitors browse the app shell on sample data.
  const { isLoaded, isSignedIn } = useAuth()
  const showSampleBanner = isLoaded && !isSignedIn

  return (
    <div className="flex h-screen overflow-hidden bg-[linear-gradient(180deg,#fbfdff_0%,#eef3f8_100%)]">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[86rem] px-5 py-6 xl:px-7">
          {showSampleBanner && <SampleBanner />}
          <Outlet />
        </div>
      </main>
    </div>
  )
}
