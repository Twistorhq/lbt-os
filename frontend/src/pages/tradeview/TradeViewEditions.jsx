import TradeViewExperience from '../../components/TradeViewExperience'
import { tradeviewApi } from '../../lib/api'

export default function TradeViewEditions() {
  return <TradeViewExperience loadConnected={tradeviewApi.pins} accountLink={`${import.meta.env.BASE_URL}sign-in`} connectedMapLink={`${import.meta.env.BASE_URL}app/tradeview`} />
}
