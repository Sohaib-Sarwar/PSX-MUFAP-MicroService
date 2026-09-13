import AccountBalanceWalletRounded from '@mui/icons-material/AccountBalanceWalletRounded'
import ShowChartRounded from '@mui/icons-material/ShowChartRounded'
import InsightsRounded from '@mui/icons-material/InsightsRounded'

const tabs = [
  { id: 'funds',   label: 'Funds',   Icon: AccountBalanceWalletRounded },
  { id: 'stocks',  label: 'Stocks',  Icon: ShowChartRounded },
  { id: 'indices', label: 'Indices', Icon: InsightsRounded },
]

export default function BottomNav({ tab, onChange }) {
  return (
    <nav className="bottom-nav">
      {tabs.map(t => (
        <button
          key={t.id}
          className={`nav-item${tab === t.id ? ' active' : ''}`}
          onClick={() => onChange(t.id)}
        >
          <t.Icon sx={{ fontSize: 22 }} />
          <span>{t.label}</span>
        </button>
      ))}
    </nav>
  )
}
