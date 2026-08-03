import { useState } from 'react'
import Home from './screens/Home'
import Execution from './screens/Execution'
import BackgroundLayer from './components/BackgroundLayer'

function App() {
  const [screen, setScreen] = useState<'home' | 'execution'>(() => window.location.hash === '#execution' ? 'execution' : 'home')
  const navigate = (next: 'home' | 'execution') => {
    window.location.hash = next === 'execution' ? 'execution' : ''
    setScreen(next)
  }
  return (
    <>
      <BackgroundLayer />
      {screen === 'home' ? <Home onNavigate={navigate} /> : <Execution onNavigate={navigate} />}
    </>
  )
}

export default App
