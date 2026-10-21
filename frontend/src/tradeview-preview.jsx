import React from 'react'
import { hydrateRoot } from 'react-dom/client'
import TradeViewExperience from './components/TradeViewExperience'
import './tradeview-preview.css'

hydrateRoot(document.getElementById('root'), <TradeViewExperience />)
document.getElementById('preview-mode-note')?.remove()
