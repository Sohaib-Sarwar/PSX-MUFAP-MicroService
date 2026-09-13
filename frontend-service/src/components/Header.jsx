import FiberManualRecordRounded from '@mui/icons-material/FiberManualRecordRounded'

export default function Header({ health }) {
  const mufapOk = health?.mufap?.ready
  const psxOk = health?.psx?.ready

  return (
    <header className="header">
      <div className="header-left">
        <img src="/icons/logo.png" alt="" className="header-logo" />
        <span className="header-title">Fintraxa</span>
      </div>
      <div className="header-right">
        <span className="health-badge" data-ok={mufapOk || false}>
          <FiberManualRecordRounded sx={{ fontSize: 8 }} />
          MF
        </span>
        <span className="health-badge" data-ok={psxOk || false}>
          <FiberManualRecordRounded sx={{ fontSize: 8 }} />
          PSX
        </span>
      </div>
    </header>
  )
}
