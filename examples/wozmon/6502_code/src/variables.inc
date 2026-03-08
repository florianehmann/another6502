xaml    = $24                   ; Examine index low byte.
xamh    = $25                   ; Examine index high byte.
stl     = $26                   ; Store address low byte.
sth     = $27                   ; Store address high byte.
l       = $28                   ; Hex value parsing low digit.
h       = $29                   ; Hex value parsing high digit.
ysav    = $2A                   ; ?
mode    = $2B                   ; $00=xam, $7F=stor, $AE=block xam

in      = $0200                 ; Input buffer, until $027F

kbd     = $2C                   ; virtual terminal input
kbdcr   = $2D                   ; virtual terminal control register
dsp     = $2E                   ; virutal terminal output register
dspcr   = $2F                   ; virtual terminal control register
