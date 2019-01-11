import torch
import torch.nn as nn

# Simplified new version based on actual results, partially adapted from PNASNet https://github.com/chenxi116/PNASNet.pytorch
OPS = {
  'none': lambda C_in, C_out, stride, affine: Zero(stride),
  'avg_pool_3x3': lambda C_in, C_out, stride, affine: nn.AvgPool2d(3, stride=stride, padding=1, count_include_pad=False) if C_in == C_out else nn.Sequential(
    nn.AvgPool2d(3, stride=stride, padding=1, count_include_pad=False),
    nn.Conv2d(C_in, C_out, 1, stride=1, padding=0, bias=False),
    nn.BatchNorm2d(C_out, eps=1e-3, affine=affine)
    ),
  'max_pool_3x3': lambda C_in, C_out, stride, affine: nn.MaxPool2d(3, stride=stride, padding=1) if C_in == C_out else nn.Sequential(
    nn.MaxPool2d(3, stride=stride, padding=1),
    nn.Conv2d(C_in, C_out, 1, stride=1, padding=0, bias=False),
    nn.BatchNorm2d(C_out, eps=1e-3, affine=affine)
    ),
  'skip_connect': lambda C_in, C_out, stride, affine: Identity() if stride == 1 else FactorizedReduce(C_in, C_out, 1, stride, 0, affine=affine),
  'sep_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, padding=1, affine=affine),
  'sep_conv_5x5': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 5, stride, 2, affine=affine),
  'sep_conv_7x7': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 7, stride, 3, affine=affine),
  'dil_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, padding=2, dilation=2, affine=affine),
  'dil_conv_5x5': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 5, stride, 4, dilation=2, affine=affine),
  'conv_7x1_1x7': lambda C_in, C_out, stride, affine: nn.Sequential(
    nn.ReLU(inplace=False),
    nn.Conv2d(C_in, C_in, (1, 7), stride=(1, stride), padding=(0, 3), bias=False),
    nn.Conv2d(C_in, C_out, (7, 1), stride=(stride, 1), padding=(3, 0), bias=False),
    nn.BatchNorm2d(C_out, eps=1e-3, affine=affine)
    ),
  'flood_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, padding=1, affine=affine, C_mid_mult=4),
  'dil_flood_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, padding=2, dilation=2, affine=affine, C_mid_mult=4),
  'choke_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, padding=1, affine=affine, C_mid=32),
  'dil_choke_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, padding=2, dilation=2, affine=affine, C_mid=32),
}
# TODO(ahundt) Only have primitives be independent? combined ops?
# TODO(ahundt) if REDUCE_OPS and OPS OR THE PRIMITIVES need to be the same size, fix that...
# REDUCE_OPS = {
#   'none': lambda C_in, C_out, stride, affine: Zero(stride),
#   'avg_pool_3x3': lambda C_in, C_out, stride, affine: nn.AvgPool2d(3, stride=stride, padding=1, count_include_pad=False) if C_in == C_out else nn.Sequential(
#     nn.AvgPool2d(3, stride=stride, padding=1, count_include_pad=False),
#     nn.Conv2d(C_in, C_out, 1, stride=1, padding=0, bias=False),
#     nn.BatchNorm2d(C_out, eps=1e-3, affine=affine)
#     ),
#   'max_pool_3x3': lambda C_in, C_out, stride, affine: nn.MaxPool2d(3, stride=stride, padding=1) if C_in == C_out else nn.Sequential(
#     nn.MaxPool2d(3, stride=stride, padding=1),
#     nn.Conv2d(C_in, C_out, 1, stride=1, padding=0, bias=False),
#     nn.BatchNorm2d(C_out, eps=1e-3, affine=affine)
#     ),
#   'skip_connect': lambda C_in, C_out, stride, affine: Identity() if stride == 1 else ReLUConvBN(C_in, C_out, 1, stride, 0, affine=affine),
#   # 'sep_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, 1, affine=affine),
#   # 'sep_conv_5x5': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 5, stride, 2, affine=affine),
#   # 'sep_conv_7x7': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 7, stride, 3, affine=affine),
#   # 'dil_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, 2, dilation=2, affine=affine),
#   # 'dil_conv_5x5': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 5, stride, 4, dilation=2, affine=affine),
#   # 'conv_7x1_1x7': lambda C_in, C_out, stride, affine: nn.Sequential(
#   #   nn.ReLU(inplace=False),
#   #   nn.Conv2d(C_in, C_in, (1, 7), stride=(1, stride), padding=(0, 3), bias=False),
#   #   nn.Conv2d(C_in, C_out, (7, 1), stride=(stride, 1), padding=(3, 0), bias=False),
#   #   nn.BatchNorm2d(C_out, eps=1e-3, affine=affine)
#   #   ),
# }
'''
# New version partially adapted from PNASNet https://github.com/chenxi116/PNASNet.pytorch
OPS = {
  'none': lambda C_in, C_out, stride, affine: Zero(stride),
  'avg_pool_3x3': lambda C_in, C_out, stride, affine: nn.AvgPool2d(3, stride=stride, padding=1, count_include_pad=False) if C_in == C_out else nn.Sequential(
    nn.AvgPool2d(3, stride=stride, padding=1, count_include_pad=False),
    nn.Conv2d(C_in, C_out, 1, stride=1, padding=0, bias=False),
    nn.BatchNorm2d(C_out, eps=1e-3, affine=affine)
    ),
  'max_pool_3x3': lambda C_in, C_out, stride, affine: nn.MaxPool2d(3, stride=stride, padding=1) if C_in == C_out else nn.Sequential(
    nn.MaxPool2d(3, stride=stride, padding=1),
    nn.Conv2d(C_in, C_out, 1, stride=1, padding=0, bias=False),
    nn.BatchNorm2d(C_out, eps=1e-3, affine=affine)
    ),
  'skip_connect': lambda C_in, C_out, stride, affine: Identity() if stride == 1 else ReLUConvBN(C_in, C_out, 1, stride, 0, affine=affine),
  'sep_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, 1, affine=affine),
  'sep_conv_5x5': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 5, stride, 2, affine=affine),
  'sep_conv_7x7': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 7, stride, 3, affine=affine),
  'dil_conv_3x3': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 3, stride, 2, dilation=2, affine=affine),
  'dil_conv_5x5': lambda C_in, C_out, stride, affine: SepConv(C_in, C_out, 5, stride, 4, dilation=2, affine=affine),
  'conv_7x1_1x7': lambda C_in, C_out, stride, affine: nn.Sequential(
    nn.ReLU(inplace=False),
    nn.Conv2d(C_in, C_in, (1, 7), stride=(1, stride), padding=(0, 3), bias=False),
    nn.Conv2d(C_in, C_out, (7, 1), stride=(stride, 1), padding=(3, 0), bias=False),
    nn.BatchNorm2d(C_out, eps=1e-3, affine=affine)
    ),
}
'''
# Old Version
DARTS_OPS = {
  'none': lambda C, C_out, stride, affine: Zero(stride),
  'avg_pool_3x3': lambda C, C_out, stride, affine: nn.AvgPool2d(3, stride=stride, padding=1, count_include_pad=False),
  'max_pool_3x3': lambda C, C_out, stride, affine: nn.MaxPool2d(3, stride=stride, padding=1),
  'skip_connect': lambda C, C_out, stride, affine: Identity() if stride == 1 else FactorizedReduce(C, C, affine=affine),
  'sep_conv_3x3': lambda C, C_out, stride, affine: SepConv(C, C, 3, stride, 1, affine=affine),
  'sep_conv_5x5': lambda C, C_out, stride, affine: SepConv(C, C, 5, stride, 2, affine=affine),
  'sep_conv_7x7': lambda C, C_out, stride, affine: SepConv(C, C, 7, stride, 3, affine=affine),
  'dil_conv_3x3': lambda C, C_out, stride, affine: DilConv(C, C, 3, stride, 2, 2, affine=affine),
  'dil_conv_5x5': lambda C, C_out, stride, affine: DilConv(C, C, 5, stride, 4, 2, affine=affine),
  'conv_7x1_1x7': lambda C, C_out, stride, affine: nn.Sequential(
    nn.ReLU(inplace=False),
    nn.Conv2d(C, C, (1, 7), stride=(1, stride), padding=(0, 3), bias=False),
    nn.Conv2d(C, C, (7, 1), stride=(stride, 1), padding=(3, 0), bias=False),
    nn.BatchNorm2d(C, affine=affine)
    ),
  'nor_conv_3x3': lambda C, C_out, stride, affine: ConvBNReLU(C, C, 3, stride, 1, affine=affine),
  'nor_conv_5x5': lambda C, C_out, stride, affine: ConvBNReLU(C, C, 5, stride, 2, affine=affine),
  'nor_conv_7x7': lambda C, C_out, stride, affine: ConvBNReLU(C, C, 7, stride, 3, affine=affine),
}

class ConvBNReLU(nn.Module):

  def __init__(self, C_in, C_out, kernel_size, stride, padding, affine=True):
    super(ConvBNReLU, self).__init__()
    self.op = nn.Sequential(
      nn.Conv2d(C_in, C_out, kernel_size, stride=stride, padding=padding, bias=False),
      nn.BatchNorm2d(C_out, affine=affine),
      nn.ReLU(inplace=False)
    )

  def forward(self, x):
    return self.op(x)

class ReLUConvBN(nn.Module):

  def __init__(self, C_in, C_out, kernel_size, stride, padding, affine=True):
    super(ReLUConvBN, self).__init__()
    self.op = nn.Sequential(
      nn.ReLU(inplace=False),
      nn.Conv2d(C_in, C_out, kernel_size, stride=stride, padding=padding, bias=False),
      nn.BatchNorm2d(C_out, affine=affine)
    )

  def forward(self, x):
    return self.op(x)


class DilConv(nn.Module):

  def __init__(self, C_in, C_out, kernel_size, stride, padding, dilation, affine=True):
    super(DilConv, self).__init__()
    self.op = nn.Sequential(
      nn.ReLU(inplace=False),
      nn.Conv2d(C_in, C_in, kernel_size=kernel_size, stride=stride, padding=padding, dilation=dilation, groups=C_in, bias=False),
      nn.Conv2d(C_in, C_out, kernel_size=1, padding=0, bias=False),
      nn.BatchNorm2d(C_out, affine=affine),
      )

  def forward(self, x):
    return self.op(x)

class SepConv(nn.Module):

  def __init__(self, C_in, C_out, kernel_size, stride, padding=1, dilation=1, affine=True, C_mid_mult=1, C_mid=None):
    super(SepConv, self).__init__()
    if C_mid is not None:
      c_mid = C_mid
    else:
      c_mid = int(C_out * C_mid_mult)
    self.op = nn.Sequential(
      nn.ReLU(inplace=False),
      nn.Conv2d(C_in, C_in, kernel_size=kernel_size, stride=stride, padding=padding, dilation=dilation, groups=C_in, bias=False),
      nn.Conv2d(C_in, c_mid, kernel_size=1, padding=0, bias=False),
      nn.BatchNorm2d(c_mid, affine=affine),
      nn.ReLU(inplace=False),
      nn.Conv2d(c_mid, c_mid, kernel_size=kernel_size, stride=1, padding=1, dilation=1, groups=c_mid, bias=False),
      nn.Conv2d(c_mid, C_out, kernel_size=1, padding=0, bias=False),
      nn.BatchNorm2d(C_out, affine=affine),
      )

  def forward(self, x):
    return self.op(x)


class Identity(nn.Module):

  def __init__(self):
    super(Identity, self).__init__()

  def forward(self, x):
    return x


class Zero(nn.Module):

  def __init__(self, stride):
    super(Zero, self).__init__()
    self.stride = stride

  def forward(self, x):
    if self.stride == 1:
      return x.mul(0.)
    return x[:,:,::self.stride,::self.stride].mul(0.)


class FactorizedReduce(nn.Module):

  def __init__(self, C_in, C_out, kernel_size=1, stride=2, padding=0, affine=True):
    super(FactorizedReduce, self).__init__()
    assert C_out % 2 == 0
    self.relu = nn.ReLU(inplace=False)
    self.conv_1 = nn.Conv2d(C_in, C_out // 2, kernel_size, stride=stride, padding=padding, bias=False)
    self.conv_2 = nn.Conv2d(C_in, C_out // 2, kernel_size, stride=stride, padding=padding, bias=False)
    self.bn = nn.BatchNorm2d(C_out, affine=affine)
    self.pad = nn.ConstantPad2d((0, 1, 0, 1), 0)

  def forward(self, x):
    x = self.relu(x)
    y = self.pad(x)
    out = torch.cat([self.conv_1(x), self.conv_2(y[:,:,1:,1:])], dim=1)
    out = self.bn(out)
    return out

